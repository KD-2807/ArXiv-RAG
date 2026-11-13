from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import json
import math
from rag_shelf.domain import Profile
from rag_shelf.storage import Repository
from rag_shelf.service import IndexService, SearchService
from rag_shelf.evaluation import ranking_metrics, RetrievalEvaluator
from test_python import FakeEmbedder, FakeExtractor



class EvaluationTests(TestCase):
    def test_metrics_match_hand_calculation_and_deduplicate(self):
        result = ranking_metrics(['noise', 'a', 'a', 'b'], {'a': 3, 'b': 1}, 3)
        self.assertEqual(result['recall_at_k'], 1)
        self.assertEqual(result['mrr_at_k'], 0.5)
        expected = (7 / math.log2(3) + 1 / math.log2(4)) / (7 + 1 / math.log2(3))
        self.assertAlmostEqual(result['ndcg_at_k'], expected)

    def test_empty_ranking_is_zero(self):
        self.assertEqual(ranking_metrics([], {'a': 1}, 5), {'recall_at_k': 0, 'mrr_at_k': 0, 'ndcg_at_k': 0})

    def test_invalid_labels_are_rejected(self):
        for relevance in ({}, {'a': 0}, {'a': 4}, {'a': -1}):
            with self.assertRaises(ValueError):
                ranking_metrics([], relevance, 5)

    def test_evaluator_records_corpus_and_subject_metrics(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paper = root / 'paper.pdf'
            paper.write_bytes(b'fixture')
            repo = Repository(root / 'index.sqlite3')
            try:
                IndexService(FakeEmbedder(), FakeExtractor(), repo, lambda *_: None).index(root, Profile('model', 'digest'))
                dataset = root / 'queries.json'
                dataset.write_text(json.dumps({'version': 1, 'documents': [{'id': 'paper', 'path': 'paper.pdf'}],
                                              'queries': [{'id': 'one', 'query': 'robot', 'subject': 'Uncategorized', 'relevance': {'paper': 3}}]}))
                result = RetrievalEvaluator(repo, SearchService(FakeEmbedder(), repo)).evaluate(dataset, 3)
                self.assertEqual(result['macro']['recall_at_k'], 1)
                self.assertIn('Uncategorized', result['by_subject'])
                self.assertEqual(len(result['corpus_sha256']['paper']), 64)
                paper.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'not indexed'):
                    RetrievalEvaluator(repo, SearchService(FakeEmbedder(), repo)).evaluate(dataset, 3)
            finally:
                repo.close()
