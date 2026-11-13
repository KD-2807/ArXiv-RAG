from pathlib import Path
import os
import signal
import shutil
import subprocess
from .domain import ConfigurationError
from .limits import EXTRACT_MEMORY_BYTES, EXTRACT_TIMEOUT_SECONDS, MAX_EXTRACT_BYTES


class PdfProcessRunner:
    def __init__(self, sandbox: str = 'limits'):
        if sandbox not in ('limits', 'bubblewrap'):
            raise ConfigurationError('PDF sandbox must be limits or bubblewrap.')
        self.sandbox = sandbox
        self._checked = False

    @staticmethod
    def require(executable: str, package: str) -> str:
        value = shutil.which(executable)
        if value is None:
            raise ConfigurationError(f'Missing {executable}. Install it with: sudo apt install {package}')
        return value

    def _sandbox_command(self, path: Path, directory: Path, command: list[str]) -> list[str]:
        wrapper = [self.require('bwrap', 'bubblewrap'), '--unshare-all', '--unshare-user',
                   '--die-with-parent', '--new-session', '--cap-drop', 'ALL', '--clearenv',
                   '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'HOME', '/nonexistent',
                   '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'OMP_THREAD_LIMIT', '2']
        for folder in ('/usr', '/bin', '/sbin', '/lib', '/lib64'):
            source = Path(folder)
            if source.is_symlink():
                wrapper += ['--symlink', os.readlink(source), folder]
            elif source.exists():
                wrapper += ['--ro-bind', folder, folder]
        for config_path in ('/etc/fonts', '/etc/ld.so.cache'):
            if Path(config_path).exists():
                wrapper += ['--ro-bind', config_path, config_path]
        wrapper += ['--proc', '/proc', '--dev', '/dev', '--dir', '/tmp',
                    '--ro-bind', str(path), '/input.pdf', '--bind', str(directory), '/work',
                    '--chdir', '/work', '--remount-ro', '/', '--']
        mapped = [value.replace(str(directory), '/work') if str(directory) in value else value for value in command]
        mapped = ['/input.pdf' if value == str(path) else value for value in mapped]
        return wrapper + mapped

    @staticmethod
    def _execute(command, errors, timeout):
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=errors, start_new_session=True,
                              env={**os.environ, 'OMP_THREAD_LIMIT': '2'}) as process:
            try:
                return process.wait(timeout=timeout)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()

    def run(self, command: list[str], path: Path, directory: Path) -> None:
        limiter = self.require('prlimit', 'util-linux')
        limits = [limiter, f'--as={EXTRACT_MEMORY_BYTES}', f'--cpu={EXTRACT_TIMEOUT_SECONDS}',
                  f'--fsize={MAX_EXTRACT_BYTES}', '--']
        with (directory / 'stderr.txt').open('w+b') as errors:
            if self.sandbox == 'bubblewrap':
                if not self._checked:
                    probe = self._sandbox_command(path, directory, ['/usr/bin/true'])
                    if self._execute(limits + probe, errors, 10):
                        errors.seek(0)
                        detail = errors.read(2000).decode(errors='replace')
                        raise ConfigurationError(f'Bubblewrap isolation is unavailable; no unsandboxed fallback was used. Check Ubuntu user-namespace/AppArmor policy: {detail}')
                    self._checked = True
                    errors.seek(0)
                    errors.truncate()
                command = self._sandbox_command(path, directory, command)
            if self._execute(limits + command, errors, EXTRACT_TIMEOUT_SECONDS):
                errors.seek(0)
                detail = errors.read(2000).decode(errors='replace')
                raise RuntimeError(f'PDF processing failed or exceeded a resource limit: {detail}')
