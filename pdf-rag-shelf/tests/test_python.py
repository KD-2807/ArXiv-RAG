from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import math
from rag_shelf.domain import Profile, chunks, vector_blob
from rag_shelf.service import IndexService, SearchService
from rag_shelf.storage import Repository
from rag_shelf.adapters import PopplerExtractor, Ollama


class FakeEmbedder:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def embed(self, texts):
        self.calls += 1
        if self.fail and self.calls > 1:
            raise RuntimeError('Inference failed')
        return [[1.0, 0.0] if 'robot' in text.lower() else [0.0, 1.0] for text in texts]


class FakeExtractor:
    def pages(self, path):
        yield 1, 'robot movement and motion'
        yield 2, 'sound synthesis and audio'


class IndexTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'papers'
        (self.root / 'Robotics').mkdir(parents=True)
        self.paper = self.root / 'Robotics/paper.pdf'
        self.paper.write_bytes(b'test PDF content')
        self.repo = Repository(Path(self.temp.name) / 'index.sqlite3')
        self.addCleanup(self.repo.close)
        self.profile = Profile('nomic-embed-text-v2-moe:latest', 'digest')
        self.embed = FakeEmbedder()
        self.service = IndexService(self.embed, FakeExtractor(), self.repo, lambda *_: None)

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def test_index_search_citations(self):
        self.assertEqual(self.service.index(self.root, self.profile), 0)
        hits = SearchService(self.embed, self.repo).search('robot', 2)
        self.assertEqual(hits[0]['page'], 1)
        self.assertEqual(hits[0]['sources'][0]['subject'], 'Robotics')
        self.assertEqual(hits[0]['sources'][0]['path'], str(self.paper))
        self.assertAlmostEqual(hits[0]['score'], 1)

    def test_second_run_does_not_embed(self):
        self.service.index(self.root, self.profile)
        before = self.embed.calls
        self.service.index(self.root, self.profile)
        self.assertEqual(self.embed.calls, before)

    def test_crosslisted_file_shares_vectors(self):
        (self.root / 'Sound').mkdir()
        (self.root / 'Sound/copy.pdf').write_bytes(self.paper.read_bytes())
        self.service.index(self.root, self.profile)
        self.assertEqual(self.repo.stats()['documents'], 1)
        self.assertEqual(self.repo.stats()['files'], 2)
        self.assertEqual(self.repo.stats()['chunks'], 2)
        hit = SearchService(self.embed, self.repo).search('sound', 1, 'Sound')[0]
        self.assertEqual(hit['sources'][0]['subject'], 'Sound')

    def test_changed_content_reindexed(self):
        self.service.index(self.root, self.profile)
        self.paper.write_bytes(b'updated content')
        self.service.index(self.root, self.profile)
        self.assertEqual(self.embed.calls, 2)
        hits = SearchService(self.embed, self.repo).search('robot', 8)
        self.assertEqual(len(hits), 2)

    def test_partial_document_rolls_back(self):
        service = IndexService(FakeEmbedder(True), FakeExtractor(), self.repo, lambda *_: None)
        self.assertEqual(service.index(self.root, self.profile, batch_size=1), 1)
        self.assertEqual(self.repo.stats()['chunks'], 0)
        self.assertEqual(self.repo.stats()['documents'], 0)
        self.assertEqual(self.repo.stats()['errors'], 1)

    def test_failed_update_not_returned_as_current(self):
        self.service.index(self.root, self.profile)
        self.paper.write_bytes(b'changed content')
        service = IndexService(FakeEmbedder(True), FakeExtractor(), self.repo, lambda *_: None)
        service.index(self.root, self.profile, batch_size=1)
        self.assertEqual(SearchService(self.embed, self.repo).search('robot', 8), [])

    def test_deleted_source_excluded(self):
        self.service.index(self.root, self.profile)
        self.paper.unlink()
        self.assertEqual(SearchService(self.embed, self.repo).search('robot', 8), [])

    def test_model_change_rejected(self):
        self.service.index(self.root, self.profile)
        with self.assertRaises(ValueError):
            self.service.index(self.root, Profile(self.profile.model, 'other-digest'))

    def test_dimension_mismatch_rejected(self):
        self.service.index(self.root, self.profile)
        with self.assertRaises(ValueError):
            self.repo.add('hash', 1, 'text', vector_blob([1, 2, 3]))

    def test_hidden_objects_skipped(self):
        (self.root / '.objects').mkdir()
        (self.root / '.objects/other.pdf').write_bytes(b'other')
        self.service.index(self.root, self.profile)
        self.assertEqual(self.repo.stats()['files'], 1)

    def test_empty_pdf_recorded(self):
        class Empty:
            def pages(self, path):
                yield 1, ''
        service = IndexService(self.embed, Empty(), self.repo, lambda *_: None)
        self.assertEqual(service.index(self.root, self.profile, batch_size=1), 1)
        self.assertEqual(self.repo.stats()['documents'], 0)

    def test_prefixes(self):
        self.assertEqual(self.profile.query('test'), 'search_query: test')
        self.assertEqual(self.profile.document('test'), 'search_document: test')
        profile = Profile('dengcao/Qwen3-Embedding-8B:Q4_K_M', 'digest')
        self.assertTrue(profile.query('test').startswith('Instruct:'))
        self.assertEqual(profile.document('test'), 'test')

    def test_unicode_chunking(self):
        parts = list(chunks('Việt Nam 日本語 robotics ' * 100, 100, 20))
        self.assertTrue(parts)
        self.assertTrue(all(len(part) <= 100 for part in parts))

    def test_bad_vector(self):
        for values in ([], [0], [math.nan], [math.inf]):
            with self.assertRaises(ValueError):
                vector_blob(values)

    def test_ollama_request_contract(self):
        client = Ollama('model')
        calls = []
        def request(endpoint, data=None):
            calls.append((endpoint, data))
            return {'embeddings': [[1, 0]]}
        client.request = request
        self.assertEqual(client.embed(['text']), [[1, 0]])
        self.assertFalse(calls[0][1]['truncate'])
        self.assertEqual(calls[0][1]['keep_alive'], '10m')

    def test_poppler_real_fixture(self):
        pages = list(PopplerExtractor().pages(Path(__file__).parent / 'fixture.pdf'))
        self.assertEqual(len(pages), 2)
        self.assertIn('robot', pages[0][1].lower())
        self.assertIn('sound', pages[1][1].lower())
