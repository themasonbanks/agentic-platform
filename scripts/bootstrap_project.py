"""Prepare a reviewed harness change in a clean existing application checkout."""
import argparse
from pathlib import Path
import subprocess

TEMPLATE_PIN = '2d26fa1817ec20dd67690bd7853917a4483e8c37'
PLATFORM_PIN = '329f8d071e027cbd2b85bca6473fd9705710daa0'


def git(root, *arguments):
    return subprocess.check_output(['git', '-c', 'core.fsmonitor=false', '-C', str(root), *arguments], stderr=subprocess.DEVNULL).decode()


def prepare(target, template, write=False):
    target, template = Path(target).resolve(strict=True), Path(template).resolve(strict=True)
    if Path(git(target, 'rev-parse', '--show-toplevel').strip()).resolve() != target:
        raise ValueError('Target must be the repository root')
    if git(target, 'status', '--porcelain', '--untracked-files=all').strip():
        raise ValueError('Target must have no existing changes')
    if (target / 'scripts').is_symlink():
        raise ValueError('Symlink scripts directory is forbidden')
    workflows = target / '.github/workflows'
    if workflows.exists() and any(workflows.glob('*.y*ml')):
        raise ValueError('Existing workflows need manual integration to avoid duplicate checks')
    for path in ('scripts/setup.sh', 'scripts/check.sh'):
        file = target / path
        if not file.is_file() or file.is_symlink() or not file.read_text().strip():
            raise ValueError('Configure real setup/check scripts before adoption')
    files = {name: git(template, 'show', TEMPLATE_PIN + ':' + name)
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
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, 'Preflight failed: ' + str(error) + '\n')
