"""Run a trusted image command in a disposable container without host file mounts."""
import io
import json
from pathlib import PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import time
import uuid

from remediation.process import ProcessStopped, ProcessWorker


def snapshot_archive(files):
    """Serialize bounded ordinary source files; never traverse a host checkout."""
    if not isinstance(files, dict) or not 1 <= len(files) <= 1000:
        raise ValueError('Explicit bounded source snapshot required')
    total, directories = 0, set()
    for name, item in files.items():
        if not isinstance(name, str):
            raise ValueError('Ordinary relative source paths required')
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or path.as_posix() != name or '..' in path.parts
                or '\\' in name or any(ord(c) < 32 for c in name) or len(name.encode()) > 100
                or {'.git', '.aws', '.ssh', '.codex', '.gnupg', 'node_modules'}.intersection(path.parts)
                or any(part == '.env' or part.startswith('.env.') for part in path.parts)
                or re.search(r'(?i)(credential|secret|id_rsa|id_ed25519)', name)):
            raise ValueError('Unsafe or sensitive source path requires manual integration')
        if (not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str)
                or item[0] not in {'100644', '100755'}
                or not isinstance(item[1], bytes) or len(item[1]) > 1024 * 1024):
            raise ValueError('Bounded ordinary source bytes and file mode required')
        total += len(item[1])
        directories.update(str(parent) for parent in path.parents if str(parent) != '.')
    if total > 16 * 1024 * 1024 or directories.intersection(files):
        raise ValueError('Snapshot size or directory/file conflict requires manual integration')
    with io.BytesIO() as buffer:
        with tarfile.open(fileobj=buffer, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for directory in sorted(directories, key=lambda name: (name.count('/'), name)):
                info = tarfile.TarInfo(directory)
                info.type, info.mode, info.uid, info.gid = tarfile.DIRTYPE, 0o755, 65534, 65534
                archive.addfile(info)
            for name, (mode, content) in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(content), 0o755 if mode == '100755' else 0o644
                info.uid, info.gid = 65534, 65534
                archive.addfile(info, io.BytesIO(content))
        result = buffer.getvalue()
    if len(result) > 20 * 1024 * 1024:
        raise ValueError('Source archive exceeds bound')
    return result


class ContainerWorker:
    def __init__(self, docker_binary, image, command, files):
        if (not isinstance(docker_binary, str) or not docker_binary.startswith('/') or '\0' in docker_binary
                or not isinstance(image, str)
                or not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}', image)
                or not isinstance(command, tuple) or not 1 <= len(command) <= 20
                or any(not isinstance(part, str) or not part or '\0' in part or len(part) > 4096 for part in command)
                or not command[0].startswith('/')):
            raise ValueError('Trusted Docker binary, pinned image and absolute image command required')
        self.docker_binary, self.image, self.command = docker_binary, image, command
        self.archive = snapshot_archive(files)
        self.last_container = None

    def run(self, request, seconds, keep_going):
        """Stage source bytes, execute with a fresh guard and verify container removal."""
        if type(seconds) not in (int, float) or not 0 < seconds <= 300 or not callable(keep_going):
            raise ValueError('Bounded runtime and controller guard required')
        payload = json.dumps(request, sort_keys=True, allow_nan=False).encode()
        if len(payload) > 16384:
            raise ValueError('Worker request exceeds bound')
        if not keep_going():
            raise ProcessStopped('Execution disabled before container creation')
        deadline = time.monotonic() + seconds
        expires = int(time.time() + seconds)
        name = self.last_container = 'agentic-worker-' + uuid.uuid4().hex
        watchdog = ('remaining=$(( $1 - $(/bin/date +%s) )); test "$remaining" -gt 0; '
                    'test "$remaining" -le 300; exec /bin/sleep "$remaining"')
        with tempfile.TemporaryDirectory(prefix='container-control-') as control:
            def invoke(arguments, data=b'', guard=keep_going, until=deadline):
                remaining = min(15, until - time.monotonic())
                if remaining <= 0:
                    raise ProcessStopped('Container deadline reached')
                return ProcessWorker((self.docker_binary, *arguments), control).run_bytes(data, remaining, guard)
            try:
                volumes = json.loads(invoke(('image', 'inspect', '--format', '{{json .Config.Volumes}}',
                                             self.image)).output)
                if volumes not in (None, {}):
                    raise ValueError('Image-declared volumes require manual review')
                invoke(('create', '--name=' + name, '--pull=never', '--network=none', '--read-only',
                        '--user=65533:65533', '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
                        '--pids-limit=64', '--memory=512m', '--memory-swap=512m', '--cpus=1',
                        '--log-driver=none', '--restart=no', '--no-healthcheck', '--ipc=private',
                        '--pid=private', '--cgroupns=private',
                        '--tmpfs=/workspace:rw,exec,nosuid,nodev,size=256m,uid=65534,gid=65534,mode=0700',
                        '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m', '--env=HOME=/workspace',
                        '--env=PATH=/usr/local/bin:/usr/bin:/bin', '--env=TMPDIR=/tmp',
                        '--label=agentic.worker=' + str(expires), '--workdir=/',
                        '--entrypoint=/bin/sh', self.image, '-ec', watchdog, 'watchdog', str(expires)))
                invoke(('start', name))
                invoke(('exec', '--user=65534:65534', '--workdir=/workspace', '-i', name,
                        '/bin/tar', '-xf', '-', '-C', '/workspace'), self.archive)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProcessStopped('Container deadline reached')
                return ProcessWorker((self.docker_binary, 'exec', '--user=65534:65534',
                                      '--workdir=/workspace', '-i', name, *self.command), control).run_bytes(
                                          payload, remaining, keep_going)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                raise ProcessStopped('Container control failed') from error
            finally:
                cleanup_deadline = time.monotonic() + 5
                try:
                    try:
                        invoke(('rm', '-f', name), guard=lambda: True, until=cleanup_deadline)
                    except ProcessStopped:
                        pass
                    found = invoke(('ps', '-aq', '--filter=name=^/' + name + '$'),
                                   guard=lambda: True, until=cleanup_deadline).output
                    if found.strip():
                        raise ProcessStopped('Container cleanup could not be verified')
                except (OSError, ValueError, ProcessStopped, subprocess.SubprocessError) as error:
                    raise ProcessStopped('Container cleanup could not be verified') from error
