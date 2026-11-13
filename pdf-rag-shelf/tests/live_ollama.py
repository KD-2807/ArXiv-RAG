from pathlib import Path
import argparse
import json
import shutil
import tempfile
import numpy as np
from rag_shelf.adapters import Ollama, PopplerExtractor
from rag_shelf.domain import Profile
from rag_shelf.processes import PdfProcessRunner
from rag_shelf.service import IndexService, SearchService
from rag_shelf.storage import Repository
from rag_shelf.retrieval import FaissBackend
from rag_shelf.ocr import OcrFallbackExtractor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='nomic-embed-text-v2-moe:latest')
    parser.add_argument('--ollama-url', default='http://127.0.0.1:11434')
    parser.add_argument('--timeout', type=int, default=60)
    parser.add_argument('--faiss', action='store_true')
    parser.add_argument('--ocr-language', default='')
    parser.add_argument('--pdf-sandbox', choices=['limits', 'bubblewrap'], default='limits')
    args = parser.parse_args()
    client = Ollama(args.model, args.ollama_url, args.timeout, '1m')
    profile = Profile(args.model, client.digest(), ocr_language=args.ocr_language)
    vectors = client.embed([profile.document('Speech recognition decodes audio.'), profile.document('Robotics uses actuators and control.')])
    assert len(vectors) == 2 and len(vectors[0]) == len(vectors[1]) > 0
    assert np.isfinite(np.asarray(vectors)).all()
    with tempfile.TemporaryDirectory(prefix='rag-live-test-') as temporary:
        base = Path(temporary)
        root = base / 'papers'
        root.mkdir()
        shutil.copyfile(Path(__file__).with_name('fixture.pdf'), root / 'fixture.pdf')
        if args.ocr_language:
            shutil.copyfile(Path(__file__).with_name('scanned.pdf'), root / 'scanned.pdf')
        repository = Repository(base / 'index.sqlite3')
        try:
            runner = PdfProcessRunner(args.pdf_sandbox)
            extractor = PopplerExtractor(runner)
            if args.ocr_language:
                extractor = OcrFallbackExtractor(extractor, runner, args.ocr_language)
            service = IndexService(client, extractor, repository, lambda *_: None)
            assert service.index(root, profile) == 0
            before = repository.stats()
            assert service.index(root, profile) == 0 and repository.stats() == before
            backend = FaissBackend(repository) if args.faiss else None
            if backend:
                backend.build()
            hits = SearchService(client, repository, backend).search_documents('speech audio', 2)
            assert hits and all(hit['sources'] and hit['page'] in (1, 2) for hit in hits)
            assert all(np.isfinite(hit['score']) for hit in hits)
            print(json.dumps({'status': 'passed', 'model': args.model, 'digest': profile.digest,
                              'dimensions': len(vectors[0]), 'stats': before, 'results': len(hits),
                              'faiss': args.faiss, 'ocr_language': args.ocr_language, 'pdf_sandbox': args.pdf_sandbox,
                              'scope': 'Live API, real PDF, indexing, deduplication and retrieval plumbing; not a relevance benchmark.'}, indent=2))
        finally:
            repository.close()


if __name__ == '__main__':
    main()
