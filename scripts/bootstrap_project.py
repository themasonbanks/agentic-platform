"""Prepare a reviewed harness change in a clean existing application checkout."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

TEMPLATE_PIN = '2d26fa1817ec20dd67690bd7853917a4483e8c37'
PLATFORM_PIN = '3f910d6dcfa3cde52b62912c4155bf5dd62ef40b'


def has_non_placeholder_command(text):
    """Reject bodies containing only comments and common no-op shell commands."""
    no_ops = {'', ':', 'true', 'exit 0', 'set -e', 'set -u', 'set -eu', 'set -ue',
              'set -o pipefail', 'set -euo pipefail', 'set -euxo pipefail'}
    for line in text.splitlines():
        for command in line.split('#', 1)[0].split(';'):
            if ' '.join(command.split()) not in no_ops:
                return True
    return False


def git(root, *arguments, no_match=False):
    """Run a bounded Git read with hooks and ambient configuration disabled."""
    with tempfile.TemporaryDirectory(prefix='bootstrap-git-') as temporary:
        environment = {'PATH': os.defpath, 'HOME': temporary, 'GIT_CONFIG_GLOBAL': os.devnull,
                       'GIT_CONFIG_NOSYSTEM': '1', 'GIT_NO_REPLACE_OBJECTS': '1',
                       'GIT_NO_LAZY_FETCH': '1', 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'}
        result = subprocess.run(['git', '--no-pager', '-c', 'core.fsmonitor=false',
                                 '-c', 'core.hooksPath=' + os.devnull, '-c', 'protocol.allow=never',
                                 '-C', str(root), *arguments], env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=False)
        if no_match and result.returncode == 1:
            return ''
        result.check_returncode()
        return result.stdout.decode()


def check_configuration(root):
    """Reject configured filters and partial clones before inspecting worktrees."""
    if git(root, 'config', '--includes', '--get-regexp',
           r'^(filter\.|remote\..*\.promisor$|extensions\.partialclone$)', no_match=True):
        raise ValueError('Configured filters or partial clones require manual integration')


def prepare(target, template, write=False):
    """Validate a clean target and optionally create pinned harness files exclusively."""
    target, template = Path(target).resolve(strict=True), Path(template).resolve(strict=True)
    if Path(git(target, 'rev-parse', '--show-toplevel').strip()).resolve() != target:
        raise ValueError('Target must be the repository root')
    for root in (target, template):
        check_configuration(root)
    if any(entry.startswith('160000 ') for entry in git(target, 'ls-files', '--stage', '-z').split('\0')):
        raise ValueError('Submodules require manual integration')
    if git(target, 'status', '--porcelain', '--untracked-files=all', '--ignore-submodules=all').strip():
        raise ValueError('Target must have no existing changes')
    if (target / 'scripts').is_symlink():
        raise ValueError('Symlink scripts directory is forbidden')
    workflows = target / '.github/workflows'
    if workflows.exists() and any(workflows.glob('*.y*ml')):
        raise ValueError('Existing workflows need manual integration to avoid duplicate checks')
    for path in ('scripts/setup.sh', 'scripts/check.sh'):
        file = target / path
        if git(target, 'ls-files', '-z', '--', path) != path + '\0':
            raise ValueError('Commit setup/check scripts before adoption')
        if git(target, 'ls-files', '-v', '-z', '--', path) != 'H ' + path + '\0':
            raise ValueError('Setup/check scripts cannot use assume-unchanged or skip-worktree flags')
        committed = git(target, 'cat-file', 'blob', 'HEAD:' + path)
        if (not file.is_file() or file.is_symlink()
                or not has_non_placeholder_command(file.read_text())
                or not has_non_placeholder_command(committed)):
            raise ValueError('Configure real setup/check scripts before adoption')
    files = {name: git(template, 'cat-file', 'blob', TEMPLATE_PIN + ':' + name)
             for name in ('AGENTS.md', '.coderabbit.yaml', 'scripts/run_ci.py', 'docs/architecture.md', 'docs/definition-of-done.md', '.github/pull_request_template.md')}
    files['.github/workflows/agentic-ci.yml'] = '''name: Agentic CI
on:
  push:
  pull_request:
permissions:
  contents: read
concurrency:
  group: agentic-${{ github.event_name }}-${{ github.event.pull_request.head.repo.full_name || github.repository }}-${{ github.head_ref || github.ref_name }}
  cancel-in-progress: true
jobs:
  quality:
    uses: themasonbanks/agentic-platform/.github/workflows/project-checks.yml@PIN
  security:
    uses: themasonbanks/agentic-platform/.github/workflows/security-checks.yml@PIN
    permissions:
      contents: read
      actions: read
      security-events: write
  ci:
    name: CI
    if: always()
    needs: [quality, security]
    runs-on: ubuntu-latest
    timeout-minutes: 2
    env:
      QUALITY: ${{ needs.quality.result }}
      SECURITY: ${{ needs.security.result }}
    steps:
      - run: |
          test "$QUALITY" = success
          test "$SECURITY" = success
'''.replace('@PIN', '@' + PLATFORM_PIN)
    files['docs/agentic-adoption.md'] = (
        '# Harness adoption\n\nTemplate commit: ' + TEMPLATE_PIN + '\n\nPlatform commit: ' + PLATFORM_PIN
        + '\n\nConfigure project architecture and real checks before merging. Verify a failing and repaired hosted PR, required CI enforcement, review and phone access. Cloud deployment, Sentry, Grafana and autonomous remediation are not enabled by this change. Complete the platform onboarding playbook before declaring adoption complete.\n')
    for name in files:
        destination = target / name
        if destination.exists() or destination.is_symlink():
            raise ValueError('Refusing to overwrite ' + name)
        for parent in destination.parents:
            if parent == target:
                break
            if parent.is_symlink():
                raise ValueError('Symlink ancestor is forbidden')
    if write:
        for name, content in files.items():
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('x') as output:
                output.write(content)
    return sorted(files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target')
    parser.add_argument('--template-checkout', required=True)
    parser.add_argument('--write', action='store_true', help='write proposed files without committing or publishing')
    args = parser.parse_args()
    try:
        for name in prepare(args.target, args.template_checkout, args.write):
            print(('prepared ' if args.write else 'would prepare ') + name)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, 'Preflight failed: ' + str(error) + '\n')
