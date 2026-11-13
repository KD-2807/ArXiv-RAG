from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
import shutil
import subprocess
from rag_shelf.adapters import PopplerExtractor
from rag_shelf.domain import Profile, vector_blob
from rag_shelf.storage import Repository
from rag_shelf.service import IndexCounts, IndexOutcome, SearchService


@skipUnless(shutil.which('pdftotext') and shutil.which('prlimit'), 'Poppler/util-linux required')
class ReadingOrderTests(TestCase):
    def test_two_column_sentence_order_before_and_after(self):
        path = Path(__file__).with_name('two_columns.pdf')
        old = subprocess.check_output(['pdftotext', '-layout', str(path), '-'], text=True)
        pages = list(PopplerExtractor().pages(path))
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0][0], 1)
        before, after = ' '.join(old.split()), ' '.join(pages[0][1].split())
        left = ['Neural speech synthesis models generate', 'natural acoustic signals from written text.',
                'Vocal duration predicts the timing of speech.', 'Speech ends with a stable acoustic waveform.']
        right = ['Robot arm control relies on inverse', 'kinematics to determine the joint angles.',
                 'Motor commands regulate arm movement.', 'Robotics ends with a stable gripper pose.']
        pairs = [a + ' ' + b for column in (left, right) for a, b in zip(column, column[1:])]
        self.assertEqual(sum(pair in before for pair in pairs), 0)
        self.assertEqual(sum(pair in after for pair in pairs), 6)
        self.assertLess(after.index(left[-1]), after.index(right[0]))


class ExtractionProfileTests(TestCase):
    def test_old_layout_index_is_searchable_but_cannot_mix_new_extraction(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / 'paper.pdf'
            pdf.write_bytes(b'fixture')
            with Repository(root / 'index.sqlite3') as repository:
                old = Profile('model', 'digest', version=2)
                repository.bind(old, root)
                with repository.transaction():
                    repository.finish('document')
                    repository.add('document', 1, 'legacy text', vector_blob([1, 0]))
                    repository.attach(pdf, 'document', 'Sound')
                class Embedder:
                    def embed(self, texts):
                        return [[1, 0] for _ in texts]
                self.assertEqual(len(SearchService(Embedder(), repository).search('query', 1)), 1)
                with self.assertRaisesRegex(ValueError, 'profile differs'):
                    repository.bind(Profile('model', 'digest'), root)
                with self.assertRaisesRegex(ValueError, 'pipeline v3'):
                    old.validate()
                self.assertEqual(repository.profile(), old)
                self.assertEqual(repository.stats()['chunks'], 1)

    def test_explicit_index_outcomes_and_invalid_outcome(self):
        counts = IndexCounts()
        for outcome in IndexOutcome:
            counts.record(outcome)
        self.assertEqual((counts.indexed, counts.skipped, counts.blocked, counts.failed), (1, 1, 1, 1))
        with self.assertRaises(ValueError):
            counts.record('indexd')
