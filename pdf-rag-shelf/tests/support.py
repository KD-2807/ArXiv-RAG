from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from rag_shelf.domain import Profile, OllamaUnavailableError
from rag_shelf.service import IndexService
from rag_shelf.storage import Repository

class ConstantEmbedder:
    def __init__(self, failure=None):
        self.inputs = []
        self.failure = failure

    def embed(self, texts):
        self.inputs.extend(texts)
        if self.failure is not None and len(self.inputs) > self.failure:
            raise OllamaUnavailableError('Temporary failure')
        return [[1.0, 0.0] for _ in texts]

class Pages:
    def __init__(self, amount=9, changed=False, interrupt=None):
        self.amount, self.changed, self.interrupt = amount, changed, interrupt
        self.closed = False

    def pages(self, path):
        try:
            for page in range(1, self.amount + 1):
                if page == self.interrupt:
                    raise KeyboardInterrupt()
                yield page, ('changed ' if self.changed else 'original ') + str(page)
        finally:
            self.closed = True

class Fixture(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'papers'
        self.root.mkdir()
        self.path = self.root / 'paper.pdf'
        self.path.write_bytes(b'original PDF bytes')
        self.repo = Repository(self.base / 'index.sqlite3')
        self.addCleanup(self.repo.close)
        self.profile = Profile('model', 'digest')

    def index(self, embedder=None, extractor=None, batch=2):
        return IndexService(embedder or ConstantEmbedder(), extractor or Pages(), self.repo, lambda *_: None).index(self.root, self.profile, batch)
