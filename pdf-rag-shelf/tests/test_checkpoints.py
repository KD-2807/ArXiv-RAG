from pathlib import Path
from unittest.mock import patch
from rag_shelf.adapters import PopplerExtractor
from rag_shelf.checkpoints import CheckpointStore
from rag_shelf.domain import checksum, ContextLengthError
from rag_shelf.service import SearchService
from rag_shelf.storage import Repository

from support import ConstantEmbedder, Pages, Fixture


class CheckpointTests(Fixture):
    def test_transient_failure_resumes_committed_batches_without_publishing_partial_document(self):
        self.assertEqual(self.index(ConstantEmbedder(failure=4)), 1)
        self.assertEqual(CheckpointStore(self.repo).stats()['pending_chunks'], 4)
        self.assertEqual(self.repo.stats()['chunks'], 0)
        self.assertFalse(self.repo.has(checksum(self.path)))
        self.assertEqual(SearchService(ConstantEmbedder(), self.repo).search('query', 3), [])
        embedder = ConstantEmbedder()
        self.assertEqual(self.index(embedder), 0)
        self.assertEqual(len(embedder.inputs), 5)
        self.assertEqual(self.repo.stats()['chunks'], 9)
        self.assertEqual(CheckpointStore(self.repo).stats()['pending_chunks'], 0)

    def test_reopening_database_resumes_with_a_different_batch_size(self):
        self.index(ConstantEmbedder(failure=4))
        database = self.repo.path
        self.repo.close()
        self.repo = Repository(database)
        self.addCleanup(self.repo.close)
        embedder = ConstantEmbedder()
        self.assertEqual(self.index(embedder, batch=3), 0)
        self.assertEqual(len(embedder.inputs), 5)
        self.assertEqual(self.repo.stats()['documents'], 1)

    def test_keyboard_interrupt_preserves_checkpoint_and_closes_extractor(self):
        pages = Pages(interrupt=5)
        with self.assertRaises(KeyboardInterrupt):
            self.index(extractor=pages)
        self.assertTrue(pages.closed)
        self.assertEqual(CheckpointStore(self.repo).stats()['pending_chunks'], 4)
        self.assertEqual(self.repo.stats()['chunks'], 0)
        embedder = ConstantEmbedder()
        self.index(embedder)
        self.assertEqual(len(embedder.inputs), 5)

    def test_changed_extracted_prefix_discards_checkpoint_and_restarts(self):
        self.index(ConstantEmbedder(failure=4))
        embedder = ConstantEmbedder()
        self.assertEqual(self.index(embedder, Pages(changed=True)), 0)
        self.assertEqual(len(embedder.inputs), 9)
        self.assertTrue(all(row[0].startswith('changed') for row in self.repo.db.execute('SELECT text FROM chunks')))

    def test_shorter_extraction_cannot_publish_old_tail(self):
        self.index(ConstantEmbedder(failure=4))
        self.assertEqual(self.index(extractor=Pages(amount=2)), 0)
        self.assertEqual(self.repo.stats()['chunks'], 2)
        self.assertEqual([row[0] for row in self.repo.db.execute('SELECT page FROM chunks ORDER BY id')], [1, 2])

    def test_pdf_byte_change_cannot_reuse_old_checkpoint(self):
        self.index(ConstantEmbedder(failure=4))
        self.path.write_bytes(b'different PDF bytes')
        embedder = ConstantEmbedder()
        self.index(embedder)
        self.assertEqual(len(embedder.inputs), 9)
        self.assertEqual(self.repo.stats()['chunks'], 9)
        self.assertEqual(CheckpointStore(self.repo).stats()['pending_chunks'], 4)

    def test_reset_only_removes_pending_work(self):
        self.index(ConstantEmbedder(failure=4))
        self.path.write_bytes(b'new bytes')
        self.index()
        before = self.repo.stats()
        result = CheckpointStore(self.repo).reset()
        self.assertEqual(result['pending_chunks'], 4)
        self.assertEqual(self.repo.stats(), before)

    def test_final_publication_failure_keeps_complete_checkpoint_for_retry(self):
        with patch.object(self.repo, 'finish', side_effect=RuntimeError('Commit publication failed')):
            self.assertEqual(self.index(), 1)
        self.assertEqual(self.repo.stats()['chunks'], 0)
        self.assertEqual(CheckpointStore(self.repo).stats()['pending_chunks'], 9)
        embedder = ConstantEmbedder()
        self.assertEqual(self.index(embedder), 0)
        self.assertEqual(embedder.inputs, [])

    def test_failure_closes_real_extraction_temporary_files(self):
        fixture = Path(__file__).with_name('fixture.pdf')
        self.path.write_bytes(fixture.read_bytes())
        self.assertEqual(self.index(ConstantEmbedder(failure=0), PopplerExtractor(), 1), 1)
        self.assertEqual(self.repo.stats()['documents'], 0)

    def test_adaptive_splits_are_not_reembedded_after_checkpoint(self):
        class SplitEmbedder(ConstantEmbedder):
            def embed(self, texts):
                if any(len(text) > 20 for text in texts):
                    raise ContextLengthError('too long')
                return super().embed(texts)
        class SplitPages(Pages):
            def pages(self, path):
                yield 1, 'a' * 40
                yield 2, 'b' * 40
                raise RuntimeError('extraction interrupted')
        self.index(SplitEmbedder(), SplitPages(), batch=1)
        self.assertEqual(CheckpointStore(self.repo).stats()['pending_chunks'], 4)
        class FinishPages(Pages):
            def pages(self, path):
                yield 1, 'a' * 40
                yield 2, 'b' * 40
                yield 3, 'last'
        embedder = SplitEmbedder()
        self.assertEqual(self.index(embedder, FinishPages(), batch=1), 0)
        self.assertEqual(embedder.inputs, ['last'])
        self.assertEqual(self.repo.stats()['chunks'], 5)
