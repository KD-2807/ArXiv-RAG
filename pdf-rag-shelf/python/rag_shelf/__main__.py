from contextlib import contextmanager
from pathlib import Path
import argparse
import base64
import fcntl
import json
import os
import sys
from .adapters import Ollama, PopplerExtractor
from .processes import PdfProcessRunner
from .ocr import OcrFallbackExtractor
from .retrieval import FaissBackend, DEFAULT_ANN_MEMORY_MIB
from .domain import Profile, Extractor
from .limits import DEFAULT_BATCH_SIZE, HTTP_TIMEOUT_SECONDS, MAX_BATCH_SIZE
from .service import IndexService, SearchService
from .storage import Repository, private_directory


class Reporter:
    def __init__(self, ui):
        self.ui = ui

    def emit(self, event, message):
        if self.ui:
            print(event + '\t' + base64.b64encode(message.encode()).decode(), flush=True)
        else:
            print(json.dumps({'event': event, 'message': message}), file=sys.stderr, flush=True)

    def hit(self, item):
        if not item['sources']:
            return
        if self.ui:
            source = item['sources'][0]
            values = [source['path'], str(item['page']), f"{item['score']:.4f}", item['text'], source['subject']]
            for alias in item['sources'][1:]:
                values.extend([alias['path'], alias['subject']])
            print('hit\t' + '\t'.join(base64.b64encode(value.encode()).decode() for value in values), flush=True)
        else:
            print(json.dumps(item), flush=True)


def arguments(argv=None):
    parser = argparse.ArgumentParser(prog='pdf-rag-shelf')
    parser.add_argument('action', choices=['index', 'search', 'stats', 'prune', 'cancel-relocation', 'build-search-index', 'evaluate', 'open-pdf', 'checkpoint-status', 'reset-checkpoints'])
    parser.add_argument('--db', required=True)
    parser.add_argument('--root')
    parser.add_argument('--model', default='nomic-embed-text-v2-moe:latest')
    parser.add_argument('--chunk', type=int, default=600)
    parser.add_argument('--overlap', type=int, default=80)
    parser.add_argument('--batch-size', type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument('--accept-source-baseline', action='store_true')
    parser.add_argument('--backend', choices=['exact', 'faiss'], default=os.environ.get('RAG_SEARCH_BACKEND', 'exact'))
    parser.add_argument('--ann-memory-mib', type=int, default=DEFAULT_ANN_MEMORY_MIB)
    parser.add_argument('--ann-ef-search', type=int, default=96)
    parser.add_argument('--pdf-sandbox', choices=['limits', 'bubblewrap'], default=os.environ.get('RAG_PDF_SANDBOX', 'limits'))
    parser.add_argument('--ocr-language', default=os.environ.get('RAG_OCR_LANGUAGE', ''))
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--pdf-path')
    parser.add_argument('--page', type=int, default=1)
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--relocate-root', action='store_true')
    parser.add_argument('--ollama-url')
    parser.add_argument('--timeout', type=int, default=HTTP_TIMEOUT_SECONDS)
    parser.add_argument('--query', default='')
    parser.add_argument('--documents', action='store_true')
    parser.add_argument('--candidate-depth', type=int, default=4096)
    parser.add_argument('--k', type=int, default=8)
    parser.add_argument('--subject', default='')
    cleanup = parser.add_mutually_exclusive_group()
    cleanup.add_argument('--dry-run', action='store_true')
    cleanup.add_argument('--apply-prune', metavar='TOKEN')
    parser.add_argument('--ui', action='store_true')
    parser.add_argument('--encoded', action='store_true')
    return parser.parse_args(argv)


def decode_arguments(args):
    if args.encoded:
        fields = ['db', 'query', 'subject']
        if args.root is not None:
            fields.append('root')
        if args.pdf_path is not None:
            fields.append('pdf_path')
        if args.action == 'index':
            fields.append('model')
        for field in fields:
            setattr(args, field, base64.b64decode(getattr(args, field), validate=True).decode())
    args.db = Path(args.db).expanduser().resolve()
    if args.root is not None:
        args.root = Path(args.root).expanduser()
    return args


@contextmanager
def locked_repository(args):
    if args.action != 'index' and not args.db.is_file():
        raise ValueError('Database does not exist. Index a PDF folder first.')
    private_directory(args.db.parent)
    descriptor = os.open(str(args.db) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, 'a+') as lock:
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('This index is in use by another process.') from error
        repository = Repository(args.db)
        try:
            yield repository
        finally:
            repository.close()


def cmd_index(args, repository, reporter):
    if args.root is None:
        raise ValueError('--root is required for indexing.')
    profile = Profile(args.model, '', args.chunk, args.overlap, ocr_language=args.ocr_language)
    profile.validate()
    if not 1 <= args.batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f'Batch size must be 1–{MAX_BATCH_SIZE}.')
    client = Ollama(args.model, args.ollama_url, args.timeout)
    profile = Profile(args.model, client.digest(), args.chunk, args.overlap, ocr_language=args.ocr_language)
    client.embed([profile.document('Embedding capability check.')])
    runner = PdfProcessRunner(args.pdf_sandbox)
    extractor: Extractor = PopplerExtractor(runner)
    if args.ocr_language:
        extractor = OcrFallbackExtractor(extractor, runner, args.ocr_language)
    failed = IndexService(client, extractor, repository, reporter.emit).index(
        args.root, profile, args.batch_size, args.retry_failed, args.relocate_root, args.accept_source_baseline)
    return 2 if failed else 0


def cmd_search(args, repository, reporter):
    profile = repository.profile()
    client = Ollama(profile.model, args.ollama_url, args.timeout, '1m')
    if client.digest() != profile.digest:
        raise ValueError('Installed model changed. Create a new index.')
    backend = FaissBackend(repository, args.ann_memory_mib, args.ann_ef_search) if args.backend == 'faiss' else None
    service = SearchService(client, repository, backend)
    results = service.search_documents(args.query, args.k, args.subject, args.candidate_depth) if args.documents else service.search(args.query, args.k, args.subject)
    for result in results:
        reporter.hit(result)
    reporter.emit('log', f'{len(results)} results returned.')
    return 0


def cmd_stats(args, repository, reporter):
    repository.profile()
    print(json.dumps(repository.stats()))
    return 0


def cmd_prune(args, repository, reporter):
    result = repository.prune(args.apply_prune)
    if args.ui and result['dry_run']:
        values = [str(result[key]) for key in ('files', 'documents', 'chunks', 'errors', 'token', 'root')]
        print('prune\t' + '\t'.join(base64.b64encode(value.encode()).decode() for value in values), flush=True)
    elif args.ui:
        reporter.emit('log', 'Cleanup applied: ' + json.dumps(result))
    else:
        print(json.dumps(result))
    return 0


def cmd_cancel_relocation(args, repository, reporter):
    repository.cancel_relocation()
    reporter.emit('log', 'Relocation staging cancelled. Original aliases and cached vectors were preserved.')
    return 0


def cmd_build_search_index(args, repository, reporter):
    repository.profile()
    print(json.dumps(FaissBackend(repository, args.ann_memory_mib, args.ann_ef_search).build(args.subject)))
    return 0


def cmd_evaluate(args, repository, reporter):
    from .evaluation import RetrievalEvaluator
    if args.dataset is None:
        raise ValueError('--dataset is required for evaluation.')
    profile = repository.profile()
    client = Ollama(profile.model, args.ollama_url, args.timeout, '10m')
    if client.digest() != profile.digest:
        raise ValueError('Installed model changed. Rebuild the index before evaluation.')
    backend = FaissBackend(repository, args.ann_memory_mib, args.ann_ef_search) if args.backend == 'faiss' else None
    result = RetrievalEvaluator(repository, SearchService(client, repository, backend)).evaluate(args.dataset, args.k, args.candidate_depth)
    print(json.dumps({**result, 'backend': args.backend, 'ann_ef_search': args.ann_ef_search if backend else None}, indent=2))
    return 0


def cmd_checkpoint_status(args, repository, reporter):
    from .checkpoints import CheckpointStore
    repository.profile()
    store = CheckpointStore(repository)
    print(json.dumps(store.reset() if args.action == 'reset-checkpoints' else store.stats()))
    return 0


def main(argv=None):
    args = arguments(argv)
    reporter = Reporter(args.ui)
    try:
        decode_arguments(args)
        if args.backend not in ('exact', 'faiss'):
            raise ValueError('RAG_SEARCH_BACKEND must be exact or faiss.')
        if args.action == 'open-pdf':
            from .viewer import PdfViewer
            reporter.emit('log', PdfViewer().open(args.pdf_path, args.page))
            return 0
        with locked_repository(args) as repository:
            command = {'index': cmd_index, 'search': cmd_search, 'stats': cmd_stats, 'prune': cmd_prune, 'cancel-relocation': cmd_cancel_relocation, 'build-search-index': cmd_build_search_index, 'evaluate': cmd_evaluate, 'checkpoint-status': cmd_checkpoint_status, 'reset-checkpoints': cmd_checkpoint_status}[args.action]
            return command(args, repository, reporter)
    except KeyboardInterrupt:
        reporter.emit('log', 'Stopped. Completed documents and embedding checkpoints are preserved.')
        return 130
    except Exception as error:
        reporter.emit('error', str(error))
        return 1


if __name__ == '__main__':
    import signal
    def terminate(signum, frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, terminate)
    raise SystemExit(main())
