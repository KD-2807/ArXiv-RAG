from pathlib import Path
from unittest import TestCase, skipUnless
import shutil
from rag_shelf.processes import PdfProcessRunner
from rag_shelf.ocr import OcrFallbackExtractor



class RealOcrTests(TestCase):
    @skipUnless(shutil.which('tesseract') and shutil.which('pdftoppm') and shutil.which('pdftotext'), 'Optional OCR tools are not installed')
    def test_real_image_only_pdf_ocr(self):
        from rag_shelf.adapters import PopplerExtractor
        path = Path(__file__).with_name('scanned.pdf')
        runner = PdfProcessRunner()
        self.assertEqual(list(PopplerExtractor(runner).pages(path))[0][1].strip(), '')
        pages = list(OcrFallbackExtractor(PopplerExtractor(runner), runner).pages(path))
        self.assertEqual(pages[0][0], 1)
        self.assertIn('LOCAL PDF RETRIEVAL', pages[0][1])
