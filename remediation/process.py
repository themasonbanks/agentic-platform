"""Bound a trusted worker command's runtime, output and inherited environment."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import tempfile
import time


class ProcessStopped(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessResult:
    output: bytes


class ProcessWorker:
    def __init__(self, command, workspace):
        if (not isinstance(command, tuple) or not 1 <= len(command) <= 32
                or any(not isinstance(part, str) or not part or len(part) > 4096 or '\0' in part for part in command)
                or not Path(command[0]).is_absolute()):
            raise ValueError('Explicit trusted absolute worker command required')
        self.command = command
        self.workspace = Path(workspace).resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError('Trusted ephemeral workspace required')

    def run(self, request, seconds, keep_going):
        if type(seconds) not in (int, float) or not 0 < seconds <= 300 or not callable(keep_going):
            raise ValueError('Bounded positive runtime and controller guard required')
        payload = json.dumps(request, sort_keys=True, allow_nan=False).encode()
        if len(payload) > 16384:
            raise ValueError('Worker request exceeds bound')
        deadline = time.monotonic() + seconds
        if not keep_going():
            raise ProcessStopped('Execution disabled before start')
        with tempfile.TemporaryDirectory(prefix='worker-state-') as temporary:
            environment = {'PATH': os.defpath, 'HOME': temporary, 'TMPDIR': temporary,
                           'LANG': 'C.UTF-8', 'GIT_CONFIG_GLOBAL': os.devnull,
                           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0'}
            with tempfile.TemporaryFile() as incoming, selectors.DefaultSelector() as selector:
                incoming.write(payload)
                incoming.seek(0)
                process = subprocess.Popen(self.command, cwd=self.workspace, env=environment,
                                           stdin=incoming, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           start_new_session=True, close_fds=True)
                output, total = bytearray(), 0
                try:
                    for stream in (process.stdout, process.stderr):
                        os.set_blocking(stream.fileno(), False)
                        selector.register(stream, selectors.EVENT_READ)
                    while selector.get_map() or process.poll() is None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ProcessStopped('Worker deadline reached')
                        if not keep_going():
                            raise ProcessStopped('Execution disabled while running')
                        for key, _ in selector.select(min(.1, remaining)):
                            chunk = os.read(key.fileobj.fileno(), 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            total += len(chunk)
                            if total > 262144:
                                raise ProcessStopped('Worker output exceeds bound')
                            if key.fileobj is process.stdout:
                                output.extend(chunk)
                    if process.returncode != 0:
                        raise ProcessStopped('Worker exited unsuccessfully')
                    return ProcessResult(bytes(output))
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
                    process.stdout.close()
                    process.stderr.close()
