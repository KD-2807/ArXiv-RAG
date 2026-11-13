from unittest import skipUnless
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import json
import numpy as np
from rag_shelf.domain import Profile, vector_blob
from rag_shelf.storage import Repository
from rag_shelf.service import SearchService
from rag_shelf.retrieval import FaissBackend



@skipUnless(importlib.util.find_spec('faiss'), 'Optional FAISS is not installed')
class FaissTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = Repository(self.base / 'index.sqlite3')
        self.addCleanup(self.repo.close)
        self.root = self.base / 'papers'
        self.root.mkdir()
        self.profile = Profile('model', 'digest')
        self.repo.bind(self.profile, self.root)
        generator = np.random.default_rng(30)
        self.query = generator.normal(size=16).tolist()
        with self.repo.transaction():
            for doc in range(20):
                path = self.root / f'{doc}.pdf'
                path.write_bytes(str(doc).encode())
                self.repo.finish(str(doc))
                self.repo.attach(path, str(doc), 'Sound' if doc % 2 else 'Vision')
                for page in range(1, 7):
                    self.repo.add(str(doc), page, f'Document {doc}', vector_blob(generator.normal(size=16).tolist()))
        query = self.query
        class Embedder:
            def embed(self, texts):
                return [query for _ in texts]
        self.embedder = Embedder()
        self.backend = FaissBackend(self.repo)

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def test_real_faiss_roundtrip_matches_exact_on_small_corpus(self):
        self.backend.build()
        exact = SearchService(self.embedder, self.repo).search('query', 10)
        actual = SearchService(self.embedder, self.repo, self.backend).search('query', 10)
        self.assertEqual([hit['chunk_id'] for hit in actual], [hit['chunk_id'] for hit in exact])
        self.assertTrue(all(abs(a['score'] - b['score']) < 1e-6 for a, b in zip(actual, exact)))

    def test_subject_cache_is_separate_and_filters_correctly(self):
        self.backend.build('Sound')
        hits = SearchService(self.embedder, self.repo, self.backend).search('query', 8, 'Sound')
        self.assertTrue(hits)
        self.assertTrue(all(source['subject'] == 'Sound' for hit in hits for source in hit['sources']))
        with self.assertRaisesRegex(ValueError, 'No ANN cache'):
            SearchService(self.embedder, self.repo, self.backend).search('query', 8, 'Vision')

    def test_mutated_vectors_make_cache_stale(self):
        self.backend.build()
        with self.repo.transaction():
            self.repo.add('0', 7, 'added', vector_blob(self.query))
        with self.assertRaisesRegex(ValueError, 'stale'):
            SearchService(self.embedder, self.repo, self.backend).search('query', 5)

    def test_alias_change_makes_cache_stale(self):
        self.backend.build()
        with self.repo.transaction():
            self.repo.attach(self.root / '0.pdf', '0', 'Changed')
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.backend._load('')

    def test_unchanged_alias_preserves_cache(self):
        self.backend.build()
        revision = self.repo.get('search_revision')
        with self.repo.transaction():
            self.repo.attach(self.root / '0.pdf', '0', 'Vision')
        self.assertEqual(revision, self.repo.get('search_revision'))
        self.assertTrue(SearchService(self.embedder, self.repo, self.backend).search('query', 5))

    def test_corrupt_cache_rejected_before_native_deserialization(self):
        self.backend.build()
        record = json.loads(self.repo.get(self.backend._key('')))
        path = self.backend.directory / record['files'][0]['name']
        data = bytearray(path.read_bytes())
        data[4] ^= 1
        path.write_bytes(data)
        with patch('faiss.read_index', side_effect=AssertionError('Must not deserialize')):
            with self.assertRaisesRegex(ValueError, 'integrity'):
                self.backend._load('')

    def test_ann_memory_budget_refuses_before_allocation(self):
        with self.assertRaisesRegex(ValueError, 'budget'):
            FaissBackend(self.repo, 16).build()

    def test_missing_sources_do_not_appear_in_ann_results(self):
        self.backend.build()
        for number in range(16):
            (self.root / f'{number}.pdf').unlink()
        hits = SearchService(self.embedder, self.repo, self.backend).search('query', 30)
        self.assertEqual(len(hits), 24)
        self.assertTrue(all(Path(source['path']).is_file() for hit in hits for source in hit['sources']))

    def test_failed_build_does_not_publish_partial_cache(self):
        with patch('faiss.write_index', side_effect=OSError('Disk full')):
            with self.assertRaises(OSError):
                self.backend.build()
        self.assertIsNone(self.repo.get(self.backend._key('')))
        self.assertEqual(list(self.backend.directory.iterdir()), [])
