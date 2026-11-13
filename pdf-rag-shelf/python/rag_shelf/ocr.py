from pathlib import Path
import re
import tempfile
from .domain import DocumentError
from .limits import MAX_PAGE_BYTES
from .lifecycle import closing_iterator


class OcrFallbackExtractor:
    def __init__(self, extractor, runner, language: str = 'eng'):
        if not re.fullmatch(r'[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*', language):
            raise ValueError('OCR language must contain Tesseract language codes, for example eng or eng+vie.')
        self.extractor, self.runner, self.language = extractor, runner, language

    def _page(self, path: Path, page: int) -> str:
        renderer = self.runner.require('pdftoppm', 'poppler-utils')
        ocr = self.runner.require('tesseract', 'tesseract-ocr')
        with tempfile.TemporaryDirectory(prefix='rag-ocr-') as temporary:
            directory = Path(temporary)
            image, text = directory / 'page', directory / 'result'
            self.runner.run([renderer, '-f', str(page), '-l', str(page), '-singlefile',
                             '-scale-to', '2400', '-gray', str(path), str(image)], path, directory)
            self.runner.run([ocr, str(image) + '.pgm', str(text), '-l', self.language, '--psm', '3'], path, directory)
            with text.with_suffix('.txt').open('rb') as stream:
                data = stream.read(MAX_PAGE_BYTES + 1)
            if len(data) > MAX_PAGE_BYTES:
                raise DocumentError('OCR page exceeds 4 MiB.')
            return data.decode('utf-8', errors='replace')

    def pages(self, path):
        path = Path(path).resolve(strict=True)
        with closing_iterator(iter(self.extractor.pages(path))) as pages:
            for page, text in pages:
                yield page, text if text.strip() else self._page(path, page)
