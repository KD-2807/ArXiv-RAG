from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import contextlib
import io
import json
import os
import shutil
import numpy as np
from rag_shelf.adapters import Ollama, PopplerExtractor
from rag_shelf.domain import Profile, OllamaUnavailableError, CircuitOpenError, ConfigurationError, vector_blob
from rag_shelf.service import IndexService, SearchService
from rag_shelf.storage import Repository
from test_python import FakeEmbedder, FakeExtractor


class SafetyTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'papers'
        self.root.mkdir()
        self.paper = self.root / 'first.pdf'
        self.paper.write_bytes(b'first')
        self.repo = Repository(self.base / 'index.sqlite3')
        self.addCleanup(self.repo.close)
        self.profile = Profile('nomic-embed-text-v2-moe:latest', 'digest')
        self.embedder = FakeEmbedder()
        self.logs = []
        self.service = IndexService(self.embedder, FakeExtractor(), self.repo, lambda *v: self.logs.append(v))

    def tearDown(self):
        self.repo.close()
        self.temporary.cleanup()

    def indexed(self):
        self.assertEqual(self.service.index(self.root, self.profile), 0)

    def test_transient_failure_retries_without_flag(self):
        class Offline:
            def embed(self, texts):
                raise OllamaUnavailableError('Connection refused')
        failing = IndexService(Offline(), FakeExtractor(), self.repo, lambda *_: None)
        self.assertEqual(failing.index(self.root, self.profile), 1)
        self.assertEqual(self.repo.db.execute('SELECT kind FROM errors').fetchone()[0], 'transient')
        self.indexed()
        self.assertEqual(self.repo.stats()['errors'], 0)
        self.assertEqual(self.repo.stats()['documents'], 1)

    def test_unknown_failure_is_not_cached(self):
        class ResourceFailure:
            def pages(self, path):
                raise RuntimeError('Resource limit or temporary extraction failure')
        failing = IndexService(self.embedder, ResourceFailure(), self.repo, lambda *_: None)
        failing.index(self.root, self.profile)
        self.indexed()
        self.assertEqual(self.repo.stats()['errors'], 0)

    def test_circuit_breaker_leaves_remaining_files_unattempted(self):
        for number in range(8):
            (self.root / f'{number}.pdf').write_bytes(str(number).encode())
        class Offline:
            calls = 0
            def embed(self, texts):
                self.calls += 1
                raise OllamaUnavailableError('GPU unavailable')
        offline = Offline()
        failing = IndexService(offline, FakeExtractor(), self.repo, lambda *_: None)
        with self.assertRaises(CircuitOpenError):
            failing.index(self.root, self.profile)
        self.assertEqual(offline.calls, 2)
        self.assertEqual(self.repo.stats()['errors'], 2)
        self.assertEqual(self.repo.get('scan_complete'), '0')
        self.indexed()
        self.assertEqual(self.repo.stats()['documents'], 9)

    def test_deterministic_cache_is_not_a_new_failure(self):
        class Empty:
            def pages(self, path):
                yield 1, ''
        service = IndexService(self.embedder, Empty(), self.repo, lambda *_: None)
        self.assertEqual(service.index(self.root, self.profile), 1)
        self.assertEqual(service.index(self.root, self.profile), 0)
        self.assertEqual(self.repo.db.execute('SELECT kind FROM errors').fetchone()[0], 'deterministic')

    def test_connection_success_resets_breaker(self):
        for number in range(3):
            (self.root / f'{number}.pdf').write_bytes(str(number).encode())
        class Alternating(FakeEmbedder):
            def embed(self, texts):
                self.calls += 1
                if self.calls % 2:
                    raise OllamaUnavailableError('Temporary')
                return [[1, 0] for _ in texts]
        service = IndexService(Alternating(), FakeExtractor(), self.repo, lambda *_: None)
        self.assertEqual(service.index(self.root, self.profile), 2)
        self.assertEqual(self.repo.stats()['documents'], 2)

    def test_prune_missing_root_preserves_everything(self):
        self.indexed()
        before = self.repo.stats()
        self.root.rename(self.base / 'unmounted')
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.repo.prune()
        self.assertEqual(self.repo.stats(), before)

    def test_prune_empty_replacement_root_is_rejected(self):
        self.indexed()
        before = self.repo.stats()
        self.root.rename(self.base / 'unmounted')
        self.root.mkdir()
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            self.repo.prune()
        self.assertEqual(self.repo.stats(), before)

    def test_prune_defaults_to_preview(self):
        self.indexed()
        self.paper.unlink()
        before = self.repo.stats()
        preview = self.repo.prune()
        self.assertTrue(preview['dry_run'])
        self.assertEqual(preview['chunks'], 2)
        self.assertEqual(self.repo.stats(), before)
        applied = self.repo.prune(preview['token'])
        self.assertFalse(applied['dry_run'])
        self.assertEqual(self.repo.stats()['chunks'], 0)

    def test_prune_stale_preview_is_rejected(self):
        self.indexed()
        self.paper.unlink()
        preview = self.repo.prune()
        self.paper.write_bytes(b'first')
        with self.assertRaisesRegex(ValueError, 'preview changed'):
            self.repo.prune(preview['token'])
        self.assertEqual(self.repo.stats()['chunks'], 2)

    def test_prune_root_loss_after_delete_rolls_back(self):
        self.indexed()
        self.paper.unlink()
        preview = self.repo.prune()
        before = self.repo.stats()
        original = self.repo.cleanup.validate_root
        calls = 0
        def check():
            nonlocal calls
            calls += 1
            if calls == 4:
                raise ValueError('Root disappeared during cleanup')
            return original()
        with patch.object(self.repo.cleanup, 'validate_root', check):
            with self.assertRaises(ValueError):
                self.repo.prune(preview['token'])
        self.assertEqual(self.repo.stats(), before)

    def test_prune_cleans_errors_without_file_aliases(self):
        self.indexed()
        missing = self.root / 'failed-and-deleted.pdf'
        with self.repo.transaction():
            self.repo.error(missing, 'No text', 'hash', 'deterministic')
            self.repo.error('scan:old', 'old scan failure', kind='diagnostic')
            self.repo.error('non-UTF8:old', 'old filename failure', kind='diagnostic')
        preview = self.repo.prune()
        self.assertEqual(preview['errors'], 3)
        self.repo.prune(preview['token'])
        self.assertEqual(self.repo.stats()['errors'], 0)
        self.assertEqual(self.repo.stats()['chunks'], 2)

    def test_interrupted_scan_blocks_prune(self):
        self.indexed()
        self.repo.bind(self.profile, self.root)
        with self.assertRaisesRegex(ValueError, 'completed scan'):
            self.repo.prune()

    def test_interrupted_relocation_preserves_active_aliases(self):
        self.indexed()
        target = self.base / 'target'
        shutil.copytree(self.root, target)
        aliases = self.repo.db.execute('SELECT * FROM files').fetchall()
        original = self.service._index_one
        def interrupted(*args):
            original(*args)
            raise KeyboardInterrupt()
        with patch.object(self.service, '_index_one', interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.service.index(target, self.profile, relocate=True)
        self.assertEqual(self.repo.db.execute('SELECT * FROM files').fetchall(), aliases)
        self.assertEqual(self.repo.get('root'), str(self.root))
        self.assertTrue(SearchService(self.embedder, self.repo).search('robot', 1))
        with self.assertRaisesRegex(ValueError, 'pending relocation'):
            self.repo.prune()
        self.service.index(target, self.profile, relocate=True)
        self.assertEqual(self.repo.get('root'), str(target))
        self.assertIsNone(self.repo.get('pending_root'))

    def test_empty_relocation_cannot_drop_existing_documents(self):
        self.indexed()
        target = self.base / 'empty'
        target.mkdir()
        with self.assertRaisesRegex(ValueError, 'no matching copy'):
            self.service.index(target, self.profile, relocate=True)
        self.assertEqual(self.repo.stats()['files'], 1)
        self.assertEqual(self.repo.get('root'), str(self.root))
        self.repo.cancel_relocation()
        self.assertIsNone(self.repo.get('pending_root'))
        self.assertEqual(self.repo.prune()['chunks'], 0)

    def test_failed_relocation_does_not_drop_old_aliases(self):
        self.indexed()
        target = self.base / 'target'
        shutil.copytree(self.root, target)
        (target / 'new.pdf').write_bytes(b'new')
        class Broken:
            def embed(self, texts):
                raise OllamaUnavailableError('Offline')
        service = IndexService(Broken(), FakeExtractor(), self.repo, lambda *_: None)
        self.assertEqual(service.index(target, self.profile, relocate=True), 1)
        self.assertEqual(self.repo.stats()['files'], 1)
        self.assertEqual(self.repo.get('root'), str(self.root))
        self.assertEqual(self.repo.get('pending_root'), str(target))

    def test_cache_survives_commit_but_not_rollback(self):
        with self.repo.transaction():
            self.repo.add('hash', 1, 'text', vector_blob([1, 0]))
        self.assertEqual(self.repo._dimensions, 2)
        with patch.object(self.repo, 'get', wraps=self.repo.get) as get:
            with self.repo.transaction():
                self.repo.add('hash', 2, 'text', vector_blob([1, 0]))
            get.assert_not_called()

    def test_main_does_not_change_umask(self):
        from rag_shelf.__main__ import main
        previous = os.umask(0o027)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['stats', '--db', str(self.base / 'absent.sqlite3')]), 1)
            after = os.umask(0o027)
            self.assertEqual(after, 0o027)
        finally:
            os.umask(previous)

    def test_host_error_identifies_environment(self):
        for value in ('0.0.0.0', '0.0.0.0:11434', ':11434'):
            with patch.dict(os.environ, {'OLLAMA_HOST': value}):
                with self.assertRaisesRegex(ConfigurationError, 'Invalid OLLAMA_HOST'):
                    Ollama('model')
                self.assertIn('127.0.0.1', Ollama('model', 'http://127.0.0.1:11434').base)

    def test_missing_prlimit_has_install_guidance(self):
        with patch('rag_shelf.processes.shutil.which', return_value=None):
            with self.assertRaisesRegex(ConfigurationError, 'sudo apt install util-linux'):
                list(PopplerExtractor().pages(self.paper))

    def test_current_pipeline_does_not_silently_mix_legacy_chunks(self):
        self.assertEqual(self.profile.version, 3)
        legacy = Profile(self.profile.model, 'digest', version=1)
        self.repo.bind(legacy, self.root)
        with self.repo.transaction():
            self.repo.add('legacy', 1, 'robot', vector_blob([1, 0]))
            self.repo.finish('legacy')
            self.repo.attach(self.paper, 'legacy', 'Uncategorized')
        self.assertTrue(SearchService(self.embedder, self.repo).search('robot', 1))
        with self.assertRaisesRegex(ValueError, 'pipeline v3'):
            self.service.index(self.root, self.profile)

    def test_old_error_kind_does_not_block_retry(self):
        self.repo.bind(self.profile, self.root)
        from rag_shelf.domain import checksum
        with self.repo.transaction():
            self.repo.error(self.paper, 'old network error', checksum(self.paper))
        self.indexed()
        self.assertEqual(self.repo.stats()['errors'], 0)

    def test_partition_matches_brute_force_with_missing_sources_and_ties(self):
        self.repo.bind(self.profile, self.root)
        rng = np.random.default_rng(29)
        expected = []
        with self.repo.transaction():
            for doc in range(12):
                path = self.root / f'{doc}.pdf'
                if doc % 3:
                    path.write_bytes(str(doc).encode())
                digest = str(doc)
                self.repo.finish(digest)
                self.repo.attach(path, digest, 'Subject')
                for page in range(1, 401):
                    value = [int(rng.integers(-2, 3)), 1, 0, 0]
                    blob = vector_blob(value)
                    self.repo.add(digest, page, 'text', blob)
                    identifier = self.repo.db.execute('SELECT last_insert_rowid()').fetchone()[0]
                    if doc % 3:
                        expected.append((float(np.frombuffer(blob, dtype='<f4')[0]), identifier))
        class Query:
            def embed(self, texts):
                return [[1, 0, 0, 0] for _ in texts]
        actual = SearchService(Query(), self.repo).search('query', 23)
        self.assertEqual([(hit['score'], hit['chunk_id']) for hit in actual], sorted(expected, reverse=True)[:23])

    def test_cli_prune_needs_explicit_preview_token(self):
        from rag_shelf.__main__ import main
        self.indexed()
        self.paper.unlink()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(['prune', '--db', str(self.repo.path), '--dry-run']), 0)
        plan = json.loads(output.getvalue())
        self.assertTrue(plan['dry_run'])
        self.assertEqual(self.repo.stats()['chunks'], 2)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['prune', '--db', str(self.repo.path), '--apply-prune', plan['token']]), 0)
        self.assertEqual(self.repo.stats()['chunks'], 0)

    def test_staged_source_disappearing_preserves_original_aliases(self):
        self.indexed()
        target = self.base / 'target'
        shutil.copytree(self.root, target)
        original = self.service._index_one
        def disappearing(*args):
            status = original(*args)
            (target / 'first.pdf').unlink()
            return status
        with patch.object(self.service, '_index_one', disappearing):
            with self.assertRaises(FileNotFoundError):
                self.service.index(target, self.profile, relocate=True)
        self.assertEqual(self.repo.stats()['files'], 1)
        self.assertEqual(self.repo.get('root'), str(self.root))
        self.assertTrue(SearchService(self.embedder, self.repo).search('robot', 1))
