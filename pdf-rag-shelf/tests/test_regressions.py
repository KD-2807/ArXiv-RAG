from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import base64
import http.client
import importlib
import io
import json
import os
import sqlite3
import struct
import urllib.error
from rag_shelf.adapters import Ollama, loopback_url
from rag_shelf.domain import DocumentError, ContextLengthError, Profile, chunks, vector_blob
from rag_shelf.service import BatchEmbeddingService, IndexService, SearchService
from rag_shelf.storage import Repository
from test_python import FakeEmbedder, FakeExtractor


class RegressionTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'papers'
        self.root.mkdir()
        self.paper = self.root / 'one.pdf'
        self.paper.write_bytes(b'fixture')
        self.repo = Repository(self.base / 'index.sqlite3')
        self.addCleanup(self.repo.close)
        self.profile = Profile('nomic-embed-text-v2-moe:latest', 'digest')
        self.embedder = FakeEmbedder()
        self.logs = []
        self.service = IndexService(self.embedder, FakeExtractor(), self.repo, lambda *v: self.logs.append(v))

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def test_batch_reduces_calls(self):
        class Pages:
            def pages(self, path):
                for page in range(1, 35):
                    yield page, 'robot'
        service = IndexService(self.embedder, Pages(), self.repo, lambda *_: None)
        self.assertEqual(service.index(self.root, self.profile, batch_size=16), 0)
        self.assertEqual(self.embedder.calls, 3)
        self.assertEqual(self.repo.stats()['chunks'], 34)

    def test_context_splits_preserve_text_and_page(self):
        class Limited:
            def embed(self, texts):
                if any(len(text) > 55 for text in texts):
                    raise ContextLengthError('input length exceeds context length')
                return [[1, 0] for _ in texts]
        original = '日本語 math Việt Nam ' * 12
        result = list(BatchEmbeddingService(Limited(), self.profile).embed([(7, original)]))
        self.assertEqual(''.join(row[1] for row in result), original)
        self.assertTrue(all(row[0] == 7 for row in result))
        self.assertGreater(len(result), 1)

    def test_unknown_error_is_not_split(self):
        class Broken:
            calls = 0
            def embed(self, texts):
                self.calls += 1
                raise RuntimeError('GPU failure')
        broken = Broken()
        with self.assertRaises(RuntimeError):
            list(BatchEmbeddingService(broken, self.profile).embed([(1, 'long ' * 100)]))
        self.assertEqual(broken.calls, 1)

    def test_split_stops_at_minimum(self):
        class Broken:
            def embed(self, texts):
                raise ContextLengthError('too many tokens')
        with self.assertRaises(ContextLengthError):
            list(BatchEmbeddingService(Broken(), self.profile).embed([(1, 'abc')]))

    def test_non_utf8_file_does_not_abort_scan(self):
        name = os.fsencode(self.root) + b'/bad\xff.pdf'
        with open(name, 'wb') as output:
            output.write(b'bad')
        self.assertEqual(self.service.index(self.root, self.profile), 1)
        self.assertEqual(self.repo.stats()['documents'], 1)
        self.assertTrue(any('non-UTF-8' in str(row) for row in self.logs))

    def test_non_utf8_directory_is_reported(self):
        name = os.fsencode(self.root) + b'/bad\xfe'
        os.mkdir(name)
        self.assertEqual(self.service.index(self.root, self.profile), 1)
        self.assertEqual(self.repo.stats()['documents'], 1)

    def test_scan_error_continues(self):
        def walk(root, onerror, followlinks):
            onerror(PermissionError(13, 'Denied', str(self.root / 'private')))
            yield str(self.root), [], ['one.pdf']
        with patch('rag_shelf.service.os.walk', walk):
            self.assertEqual(self.service.index(self.root, self.profile), 1)
        self.assertEqual(self.repo.stats()['documents'], 1)

    def test_failed_hash_is_skipped_until_retry(self):
        class DeterministicFailure(FakeEmbedder):
            def embed(self, texts):
                self.calls += 1
                raise DocumentError('Deterministic test failure')
        failing = DeterministicFailure()
        service = IndexService(failing, FakeExtractor(), self.repo, lambda *_: None)
        self.assertEqual(service.index(self.root, self.profile, batch_size=1), 1)
        before = failing.calls
        service.index(self.root, self.profile, batch_size=1)
        self.assertEqual(failing.calls, before)
        self.assertEqual(self.service.index(self.root, self.profile, retry_failed=True), 0)
        self.assertEqual(self.repo.stats()['errors'], 0)

    def test_changed_failed_hash_retries(self):
        failing = IndexService(FakeEmbedder(True), FakeExtractor(), self.repo, lambda *_: None)
        failing.index(self.root, self.profile, batch_size=1)
        self.paper.write_bytes(b'changed')
        self.assertEqual(self.service.index(self.root, self.profile), 0)

    def test_relocate_reuses_vectors(self):
        self.service.index(self.root, self.profile)
        calls = self.embedder.calls
        moved = self.base / 'moved'
        self.root.rename(moved)
        with self.assertRaises(ValueError):
            self.service.index(moved, self.profile)
        self.service.index(moved, self.profile, relocate=True)
        self.assertEqual(self.embedder.calls, calls)
        hit = SearchService(self.embedder, self.repo).search('robot', 1)[0]
        self.assertEqual(hit['sources'][0]['path'], str(moved / 'one.pdf'))

    def test_prune_retains_valid_shared_content(self):
        copy = self.root / 'two.pdf'
        copy.write_bytes(self.paper.read_bytes())
        self.service.index(self.root, self.profile)
        self.paper.unlink()
        preview = self.repo.prune()
        self.assertTrue(preview['dry_run'])
        applied = self.repo.prune(preview['token'])
        self.assertEqual({key: applied[key] for key in ('files', 'documents', 'chunks')}, {'files': 1, 'documents': 0, 'chunks': 0})
        copy.unlink()
        applied = self.repo.prune(self.repo.prune()['token'])
        self.assertEqual({key: applied[key] for key in ('files', 'documents', 'chunks')}, {'files': 1, 'documents': 1, 'chunks': 2})

    def test_dimension_cache_recovers_after_rollback(self):
        with self.assertRaises(RuntimeError):
            with self.repo.transaction():
                self.repo.add('hash', 1, 'text', vector_blob([1, 0]))
                raise RuntimeError('rollback')
        with self.repo.transaction():
            self.repo.add('other', 1, 'text', vector_blob([1, 0, 0]))
        self.assertEqual(self.repo.get('dimensions'), '3')

    def test_source_lookup_is_cached(self):
        class Pages:
            def pages(self, path):
                for page in range(1, 101):
                    yield page, 'robot'
        IndexService(self.embedder, Pages(), self.repo, lambda *_: None).index(self.root, self.profile)
        with patch.object(self.repo, 'sources', wraps=self.repo.sources) as sources:
            SearchService(self.embedder, self.repo).search('robot', 5)
            self.assertLessEqual(sources.call_count, 6)

    def test_deleted_after_selection_is_skipped(self):
        self.service.index(self.root, self.profile)
        original = self.repo.sources
        calls = 0
        def disappearing(digest, subject):
            nonlocal calls
            calls += 1
            if calls > 1:
                self.paper.unlink(missing_ok=True)
            return original(digest, subject)
        with patch.object(self.repo, 'sources', disappearing):
            self.assertEqual(SearchService(self.embedder, self.repo).search('robot', 2), [])

    def test_empty_source_report_is_safe(self):
        from rag_shelf.__main__ import Reporter
        Reporter(True).hit({'sources': []})

    def test_main_import_has_no_cli_side_effect(self):
        with patch('sys.argv', ['app', 'invalid']):
            importlib.reload(importlib.import_module('rag_shelf.__main__'))

    def test_encoded_paths_remain_strings_until_decoded(self):
        from rag_shelf.__main__ import arguments, decode_arguments
        path = str(self.base / '日本語' / 'index.sqlite3')
        encoded = base64.b64encode(path.encode()).decode()
        args = arguments(['stats', '--db', encoded, '--encoded'])
        self.assertIsInstance(args.db, str)
        self.assertEqual(decode_arguments(args).db, Path(path))

    def test_private_permissions(self):
        path = self.base / 'private' / 'nested' / 'index.sqlite3'
        previous = os.umask(0o022)
        try:
            repository = Repository(path)
        finally:
            os.umask(previous)
        repository.close()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(path.parent.parent.stat().st_mode & 0o777, 0o700)

    def test_old_errors_table_migrates(self):
        path = self.base / 'old.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE errors(path TEXT PRIMARY KEY,message TEXT NOT NULL)')
            db.execute("INSERT INTO errors VALUES('old.pdf','old error')")
        repository = Repository(path)
        self.assertEqual(repository.stats()['errors'], 1)
        self.assertFalse(repository.failed('old.pdf', 'hash'))
        repository.close()

    def test_loopback_configuration(self):
        self.assertEqual(loopback_url('127.0.0.1:80'), 'http://127.0.0.1:80/api/')
        self.assertEqual(loopback_url('localhost:11435'), 'http://127.0.0.1:11435/api/')
        self.assertEqual(loopback_url('http://[::1]:11434'), 'http://[::1]:11434/api/')
        for value in ['127.0.0.1:0', '0.0.0.0:11434', '192.168.1.2:11434', 'https://127.0.0.1', 'http://a@127.0.0.1', 'http://127.0.0.1/path']:
            with self.assertRaises(ValueError):
                loopback_url(value)

    def test_interrupted_body_is_retried(self):
        class Response:
            def __init__(self, error=None): self.error = error
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size):
                if self.error: raise self.error
                return b'{"models": []}'
        for error in [TimeoutError(), ConnectionResetError(), http.client.RemoteDisconnected(), http.client.IncompleteRead(b'partial')]:
            client = Ollama('model')
            with patch.object(client.client, 'open', side_effect=[Response(error), Response()]) as request, patch('rag_shelf.adapters.time.sleep'):
                self.assertEqual(client.request('tags'), {'models': []})
                self.assertEqual(request.call_count, 2)

    def test_context_http_error_is_typed(self):
        client = Ollama('model')
        error = urllib.error.HTTPError(client.base, 400, 'Bad input', {}, io.BytesIO(b'input length exceeds context length'))
        with patch.object(client.client, 'open', side_effect=error) as request:
            with self.assertRaises(ContextLengthError): client.embed(['text'])
            self.assertEqual(request.call_count, 1)

    def test_conformance_fixtures(self):
        fixture = json.loads((Path(__file__).parent / 'conformance.json').read_text())
        for case in fixture['chunks']:
            self.assertEqual(list(chunks(case['text'], case['size'], case['overlap'])), case['expected'])
        for case in fixture['vectors']:
            blob = vector_blob(case['input'])
            values = struct.unpack('<' + 'f' * len(case['input']), blob)
            for actual, expected in zip(values, case['expected']):
                self.assertAlmostEqual(actual, expected, places=6)
