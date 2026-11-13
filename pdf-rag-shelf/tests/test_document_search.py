from unittest import skipUnless
import importlib.util
import json
import numpy as np
from rag_shelf.domain import vector_blob, checksum
from rag_shelf.evaluation import RetrievalEvaluator
from rag_shelf.retrieval import FaissBackend
from rag_shelf.service import SearchService

from support import ConstantEmbedder, Fixture


class DocumentSearchTests(Fixture):
    def populate(self):
        self.repo.bind(self.profile, self.root)
        self.second = self.root / 'second.pdf'
        self.third = self.root / 'third.pdf'
        self.second.write_bytes(b'second')
        self.third.write_bytes(b'third')
        with self.repo.transaction():
            for path, value, amount in [(self.path, [1, 0], 450), (self.second, [0.9, 0.1], 2), (self.third, [0.8, 0.2], 3)]:
                digest = checksum(path)
                self.repo.finish(digest)
                self.repo.attach(path, digest, 'Topic')
                for page in range(amount):
                    self.repo.add(digest, page + 1, path.stem, vector_blob(value))

    def test_one_paper_cannot_dominate_document_results(self):
        self.populate()
        service = SearchService(ConstantEmbedder(), self.repo)
        chunks = service.search('query', 100)
        self.assertEqual(len({hit['document_id'] for hit in chunks}), 1)
        documents = service.search_documents('query', 3)
        self.assertEqual([hit['document_id'] for hit in documents], [checksum(path) for path in [self.path, self.second, self.third]])
        self.assertEqual(service.last_search['candidate_chunks'], 455)

    def test_evaluation_finds_relevant_paper_beyond_top_100_chunks(self):
        self.populate()
        dataset = self.base / 'dataset.json'
        dataset.write_text(json.dumps({'version': 1, 'documents': [{'id': 'second', 'path': 'second.pdf'}],
                                      'queries': [{'id': 'q1', 'query': 'test', 'relevance': {'second': 3}}]}))
        report = RetrievalEvaluator(self.repo, SearchService(ConstantEmbedder(), self.repo)).evaluate(dataset, 3)
        self.assertEqual(report['macro']['recall_at_k'], 1)
        self.assertEqual(report['macro']['mrr_at_k'], 0.5)
        self.assertEqual(report['ranking_unit'], 'document')

    def test_missing_best_source_does_not_remove_other_documents(self):
        self.populate()
        self.path.unlink()
        result = SearchService(ConstantEmbedder(), self.repo).search_documents('query', 3)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['document_id'], checksum(self.second))

    def test_grouped_ranking_matches_full_reference_with_ties_and_updates(self):
        from rag_shelf.ranking import DocumentRanking
        generator = np.random.default_rng(14)
        ranking, reference = DocumentRanking(11), {}
        for identifier in range(10000):
            digest = str(int(generator.integers(0, 500)))
            score = float(generator.integers(-2, 9))
            if int(digest) % 7:
                reference[digest] = max(reference.get(digest, (-float('inf'), -1)), (score, identifier))
            ranking.offer(digest, score, identifier, lambda value: int(value) % 7 != 0)
            self.assertLessEqual(len(ranking.items), 11)
        self.assertEqual(ranking.results(), sorted(reference.values(), reverse=True)[:11])

    @skipUnless(importlib.util.find_spec('faiss'), 'Optional FAISS is not installed')
    def test_ann_deduplicates_then_expands_candidates(self):
        self.populate()
        backend = FaissBackend(self.repo)
        backend.build()
        result = SearchService(ConstantEmbedder(), self.repo, backend).search_documents('query', 3, candidate_depth=1000)
        self.assertEqual(len(result), 3)
        self.assertGreater(backend.last_search['candidate_chunks'], 100)
        self.assertIn(backend.last_search['method'], ('faiss', 'exact'))

    @skipUnless(importlib.util.find_spec('faiss'), 'Optional FAISS is not installed')
    def test_ann_falls_back_when_candidate_limit_has_too_few_documents(self):
        self.populate()
        backend = FaissBackend(self.repo, search_effort=512)
        backend.build()
        result = SearchService(ConstantEmbedder(), self.repo, backend).search_documents('query', 3, candidate_depth=100)
        self.assertEqual(len(result), 3)
        self.assertTrue(backend.last_search['exact_fallback'])
        self.assertEqual(backend._load('')[0].hnsw.efSearch, 512)
