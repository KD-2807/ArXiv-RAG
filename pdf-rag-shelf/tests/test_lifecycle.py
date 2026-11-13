from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import contextlib
import io
import os
import sqlite3
import subprocess
import sys
from rag_shelf.processes import PdfProcessRunner
from rag_shelf.storage import Repository



class LifecycleTests(TestCase):
    def test_repository_context_closes_after_exception_and_close_is_idempotent(self):
        with TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                with Repository(Path(temporary) / 'index.sqlite3') as repository:
                    raise RuntimeError('failed')
            with self.assertRaises(sqlite3.ProgrammingError):
                repository.db.execute('SELECT 1')
            repository.close()

    def test_schema_failure_closes_constructed_connection(self):
        with TemporaryDirectory() as temporary:
            connection = sqlite3.connect(Path(temporary) / 'index.sqlite3')
            with patch('rag_shelf.storage.sqlite3.connect', return_value=connection), patch('rag_shelf.storage.SCHEMA', 'INVALID SQL'):
                with self.assertRaises(sqlite3.OperationalError):
                    Repository(Path(temporary) / 'index.sqlite3')
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')

    def test_keyboard_interrupt_cli_closes_connection(self):
        from rag_shelf.__main__ import main
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / 'index.sqlite3'
            with Repository(database):
                pass
            captured = []
            def interrupted(args, repository, reporter):
                captured.append(repository.db)
                raise KeyboardInterrupt()
            with patch('rag_shelf.__main__.cmd_stats', interrupted), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['stats', '--db', str(database)]), 130)
            with self.assertRaises(sqlite3.ProgrammingError):
                captured[0].execute('SELECT 1')

    def test_parser_timeout_kills_process_group(self):
        with TemporaryDirectory() as temporary:
            pidfile = Path(temporary) / 'pid'
            script = 'import os,time;from pathlib import Path;Path(' + repr(str(pidfile)) + ').write_text(str(os.getpid()));time.sleep(30)'
            with (Path(temporary) / 'errors').open('w+b') as errors:
                with self.assertRaises(subprocess.TimeoutExpired):
                    PdfProcessRunner._execute([sys.executable, '-c', script], errors, 0.4)
            pid = int(pidfile.read_text())
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_parser_interrupt_kills_and_reaps_child(self):
        original_wait = subprocess.Popen.wait
        captured = []
        def interrupted(process, timeout=None):
            captured.append(process.pid)
            if timeout is not None:
                raise KeyboardInterrupt()
            return original_wait(process, timeout)
        with TemporaryDirectory() as temporary, (Path(temporary) / 'errors').open('w+b') as errors:
            with patch.object(subprocess.Popen, 'wait', interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    PdfProcessRunner._execute([sys.executable, '-c', 'import time;time.sleep(30)'], errors, 10)
        with self.assertRaises(ProcessLookupError):
            os.kill(captured[0], 0)
