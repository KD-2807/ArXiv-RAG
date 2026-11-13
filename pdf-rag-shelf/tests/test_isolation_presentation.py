from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import base64
import contextlib
import io
from rag_shelf.domain import Profile, ConfigurationError
from rag_shelf.processes import PdfProcessRunner
from rag_shelf.ocr import OcrFallbackExtractor
from rag_shelf.viewer import PdfViewer



class IsolationAndPresentationTests(TestCase):
    def test_bubblewrap_has_no_host_home_network_or_database_mount(self):
        runner = PdfProcessRunner('bubblewrap')
        with patch.object(runner, 'require', return_value='/usr/bin/bwrap'):
            command = runner._sandbox_command(Path('/private/secret.pdf'), Path('/tmp/work'), ['/usr/bin/pdftotext', '/private/secret.pdf', '/tmp/work/result.txt'])
        self.assertIn('--unshare-all', command)
        self.assertIn('--clearenv', command)
        self.assertIn('--remount-ro', command)
        self.assertEqual(command[-3:], ['/usr/bin/pdftotext', '/input.pdf', '/work/result.txt'])
        self.assertNotIn('/home', command)
        self.assertNotIn('--share-net', command)

    def test_bubblewrap_failure_has_no_unsandboxed_retry(self):
        runner = PdfProcessRunner('bubblewrap')
        with TemporaryDirectory() as temporary, patch.object(runner, 'require', return_value='/usr/bin/bwrap'):
            with patch.object(runner, '_execute', return_value=1) as run:
                with self.assertRaisesRegex(ConfigurationError, 'no unsandboxed fallback'):
                    runner.run(['/usr/bin/true'], Path('/input.pdf'), Path(temporary))
                self.assertEqual(run.call_count, 1)

    def test_ocr_fills_only_empty_pages(self):
        class Extractor:
            def pages(self, path):
                yield 1, 'Existing text'
                yield 2, ''
                yield 3, 'More text'
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'scan.pdf'
            path.write_bytes(b'pdf')
            extractor = OcrFallbackExtractor(Extractor(), PdfProcessRunner())
            with patch.object(extractor, '_page', return_value='Recognized page') as ocr:
                self.assertEqual(list(extractor.pages(path)), [(1, 'Existing text'), (2, 'Recognized page'), (3, 'More text')])
                ocr.assert_called_once_with(path, 2)

    def test_ocr_profile_does_not_mix_with_text_only_index(self):
        normal = Profile('model', 'digest')
        ocr = Profile('model', 'digest', ocr_language='eng')
        self.assertNotEqual(normal.json(), ocr.json())
        self.assertNotIn('ocr_language', normal.json())
        with self.assertRaises(ValueError):
            OcrFallbackExtractor(None, None, '../escape')

    def test_ui_protocol_contains_every_alias(self):
        from rag_shelf.__main__ import Reporter
        item = {'page': 3, 'score': 0.6, 'text': 'passage', 'sources': [
            {'path': '/papers/first {copy}.pdf', 'subject': 'Sound'},
            {'path': '/papers/second.pdf', 'subject': 'Robotics'}]}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            Reporter(True).hit(item)
        fields = [base64.b64decode(value).decode() for value in output.getvalue().strip().split('\t')[1:]]
        self.assertEqual(fields[5:], ['/papers/second.pdf', 'Robotics'])

    def test_page_navigation_uses_argument_list(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'a ; $(touch pwned).pdf'
            path.write_bytes(b'pdf')
            with patch('rag_shelf.viewer.shutil.which', side_effect=lambda x: '/usr/bin/evince' if x == 'evince' else None):
                command, supported = PdfViewer().command(path, 12)
                self.assertTrue(supported)
                self.assertEqual(command, ['/usr/bin/evince', '--page-index', '11', str(path)])

    def test_page_navigation_default_viewer_reports_limit(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'paper.pdf'
            path.write_bytes(b'pdf')
            with patch('rag_shelf.viewer.shutil.which', side_effect=lambda x: '/usr/bin/xdg-open' if x == 'xdg-open' else None):
                command, supported = PdfViewer().command(path, 3)
                self.assertFalse(supported)
                self.assertEqual(command[-1], str(path))
