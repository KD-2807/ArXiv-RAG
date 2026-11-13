from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterator
from pathlib import Path
import hashlib
import json
import heapq
import os
import numpy as np
from .domain import (
    chunks, checksum, vector_blob, Embedder, Extractor, ContextLengthError,
    DocumentError, OllamaUnavailableError, ConfigurationError, CircuitOpenError, Profile,
)
from .storage import Repository
from .filesystem import root_identity
from .ranking import DocumentRanking
from .checkpoints import CheckpointStore, CheckpointMismatch
from .lifecycle import closing_iterator
from .limits import (
    DEFAULT_BATCH_SIZE, MAX_BATCH_SIZE, MIN_SPLIT_CHARACTERS, SEARCH_BATCH_SIZE,
    SOURCE_CACHE_SIZE, MAX_SEARCH_RESULTS, SEARCH_MATRIX_BYTES, CIRCUIT_FAILURE_LIMIT,
)


class BatchEmbeddingService:
    def __init__(self, embedder, profile):
        self.embedder, self.profile = embedder, profile

    def embed(self, items):
        try:
            values = self.embedder.embed([self.profile.document(text) for _, text in items])
            if len(values) != len(items):
                raise OllamaUnavailableError('Embedding result count differs from batch size.')
        except ContextLengthError:
            if len(items) > 1:
                midpoint = len(items) // 2
                yield from self.embed(items[:midpoint])
                yield from self.embed(items[midpoint:])
            else:
                page, text = items[0]
                if len(text) <= MIN_SPLIT_CHARACTERS:
                    raise
                midpoint = len(text) // 2
                yield from self.embed([(page, text[:midpoint]), (page, text[midpoint:])])
            return
        for (page, text), value in zip(items, values):
            try:
                blob = vector_blob(value)
            except (ValueError, TypeError) as error:
                raise OllamaUnavailableError(f'Ollama returned an invalid vector: {error}') from error
            yield page, text, blob


class IndexOutcome(Enum):
    INDEXED = 'indexed'
    SKIPPED = 'skipped'
    BLOCKED = 'blocked'
    FAILED = 'failed'


@dataclass
class IndexCounts:
    indexed: int = 0
    skipped: int = 0
    blocked: int = 0
    failed: int = 0
    scan_errors: int = 0

    def record(self, outcome: IndexOutcome) -> None:
        match outcome:
            case IndexOutcome.INDEXED:
                self.indexed += 1
            case IndexOutcome.SKIPPED:
                self.skipped += 1
            case IndexOutcome.BLOCKED:
                self.blocked += 1
            case IndexOutcome.FAILED:
                self.failed += 1
            case _:
                raise ValueError('Unknown indexing outcome.')


class IndexService:
    def __init__(self, embedder: Embedder, extractor: Extractor, repository: Repository, emit: Callable):
        self.embedder, self.extractor, self.repository, self.emit = embedder, extractor, repository, emit

    @staticmethod
    def _safe(value) -> str:
        return str(value).encode('utf-8', errors='backslashreplace').decode()

    def _record_error(self, path, error, digest: str | None = None, kind: str = 'unknown') -> None:
        safe_path, message = self._safe(path), self._safe(error)
        with self.repository.transaction():
            self.repository.invalidate(safe_path)
            self.repository.error(safe_path, message, digest, kind)
        self.emit('log', f'Failed ({kind}): {safe_path}: {message}')

    def _valid_name(self, path: Path, report: Callable) -> bool:
        try:
            str(path).encode('utf-8')
            return True
        except UnicodeEncodeError:
            report('non-UTF8:' + str(path), 'Skipped a non-UTF-8 path. Rename it to index its PDFs.')
            return False

    def _scan(self, root: Path, report: Callable) -> Iterator[Path]:
        def on_error(error):
            report('scan:' + str(error.filename), error)
        for directory, folders, names in os.walk(root, onerror=on_error, followlinks=False):
            folders[:] = [name for name in sorted(folders)
                          if not name.startswith('.') and not (Path(directory) / name).is_symlink()
                          and self._valid_name(Path(directory) / name, report)]
            for name in sorted(names):
                path = Path(directory) / name
                if name.startswith('.') or path.suffix.lower() != '.pdf' or path.is_symlink():
                    continue
                if self._valid_name(path, report):
                    yield path

    def _resume_document(self, path: Path, digest: str, subject: str, profile: Profile, batch_size: int) -> tuple[int, int]:
        checkpoints = CheckpointStore(self.repository)
        state = checkpoints.load(digest, profile)
        if state.consumed:
            self.emit('log', f'Resuming {path.name}: {state.consumed} input chunks already embedded.')
        batcher = BatchEmbeddingService(self.embedder, profile)
        prefix = hashlib.sha256()
        consumed = empty = 0
        saved = state.consumed
        batch = []
        with closing_iterator(iter(self.extractor.pages(path))) as pages:
            for page, text in pages:
                empty += not text.strip()
                for segment in chunks(text, profile.chunk, profile.overlap):
                    prefix.update(json.dumps([page, segment], ensure_ascii=False).encode())
                    prefix.update(b'\n')
                    consumed += 1
                    if consumed <= state.consumed:
                        if consumed == state.consumed and prefix.hexdigest() != state.prefix:
                            raise CheckpointMismatch('Extracted text changed since the checkpoint.')
                        continue
                    batch.append((page, segment))
                    if len(batch) == batch_size:
                        with closing_iterator(batcher.embed(batch)) as values:
                            checkpoints.save(digest, saved, consumed, prefix.hexdigest(), values)
                        saved = consumed
                        batch.clear()
            if consumed < state.consumed:
                raise CheckpointMismatch('Extracted text is shorter than the checkpoint.')
            if batch:
                with closing_iterator(batcher.embed(batch)) as values:
                    checkpoints.save(digest, saved, consumed, prefix.hexdigest(), values)
        count = checkpoints.count(digest)
        if count == 0:
            raise DocumentError('No extractable text. OCR is required for scanned PDFs.')
        if checksum(path) != digest:
            checkpoints.discard(digest)
            raise RuntimeError('PDF changed during indexing; retry later.')
        checkpoints.publish(digest, path, subject)
        return count, empty

    def _write_document(self, path: Path, digest: str, subject: str, profile: Profile, batch_size: int) -> tuple[int, int]:
        for attempt in range(2):
            try:
                return self._resume_document(path, digest, subject, profile, batch_size)
            except CheckpointMismatch:
                CheckpointStore(self.repository).discard(digest)
                if attempt:
                    raise
                self.emit('log', f'Checkpoint text changed; restarting {path.name}.')
            except DocumentError:
                CheckpointStore(self.repository).discard(digest)
                raise
        raise RuntimeError('Unable to resume document.')

    def _index_one(self, path: Path, root: Path, profile: Profile, batch_size: int, retry_failed: bool) -> IndexOutcome:
        digest = None
        subject = path.relative_to(root).parts[0] if path.parent != root else 'Uncategorized'
        try:
            digest = checksum(path)
            if self.repository.has(digest):
                with self.repository.transaction():
                    self.repository.attach(path, digest, subject)
                self.emit('log', f'Skipped unchanged content: {path.name}')
                return IndexOutcome.SKIPPED
            if not retry_failed and self.repository.failed(path, digest):
                self.emit('log', f'Skipped known document error: {path.name}. Use --retry-failed after fixing it.')
                return IndexOutcome.BLOCKED
            with self.repository.transaction():
                self.repository.invalidate(path)
            self.emit('log', f'Indexing: {path.name}')
            count, empty = self._write_document(path, digest, subject, profile, batch_size)
            self.emit('log', f'Indexed {count} chunks; {empty} empty pages: {path.name}')
            return IndexOutcome.INDEXED
        except Exception as error:
            kind = 'deterministic' if isinstance(error, DocumentError) else 'unknown'
            if isinstance(error, OllamaUnavailableError):
                kind = 'transient'
            self._record_error(path, error, digest, kind)
            if isinstance(error, (OllamaUnavailableError, ConfigurationError)):
                raise
            return IndexOutcome.FAILED

    def index(self, root, profile: Profile, batch_size: int = DEFAULT_BATCH_SIZE,
              retry_failed: bool = False, relocate: bool = False, accept_source_baseline: bool = False) -> int:
        root = Path(root).expanduser().resolve(strict=True)
        str(root).encode('utf-8')
        identity = root_identity(root)
        profile.validate()
        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            raise ValueError(f'Batch size must be 1–{MAX_BATCH_SIZE}.')
        moving = self.repository.bind(profile, root, relocate, accept_source_baseline)
        counts = IndexCounts()
        consecutive = 0
        def report(path, error):
            counts.failed += 1
            counts.scan_errors += 1
            self._record_error(path, error, kind='diagnostic')
        for path in self._scan(root, report):
            try:
                status = self._index_one(path, root, profile, batch_size, retry_failed)
            except OllamaUnavailableError as error:
                counts.failed += 1
                consecutive += 1
                if consecutive >= CIRCUIT_FAILURE_LIMIT:
                    raise CircuitOpenError(
                        f'Stopped after {consecutive} consecutive Ollama failures. '
                        'Remaining PDFs were not attempted. Restore Ollama and run indexing again; no retry flag is needed.'
                    ) from error
                continue
            counts.record(status)
            if status is IndexOutcome.INDEXED:
                consecutive = 0
        self.emit('log', f'Finished: indexed={counts.indexed}, skipped={counts.skipped}, known_document_errors={counts.blocked}, new_failures={counts.failed}')
        if moving and (counts.failed or counts.blocked):
            self.emit('log', 'Relocation remains staged. Original aliases are unchanged. Resume --relocate-root after fixing errors, or run cancel-relocation.')
            return max(1, counts.failed)
        if not counts.scan_errors:
            self.repository.finish_scan(root, identity)
        return counts.failed


class SearchService:
    def __init__(self, embedder: Embedder, repository, backend=None):
        self.embedder, self.repository = embedder, repository
        self.backend = backend
        self.last_search: dict[str, object] = {}

    def _source_available(self, digest: str, subject: str, cache: OrderedDict) -> bool:
        if digest not in cache:
            cache[digest] = bool(self.repository.sources(digest, subject))
            if len(cache) > SOURCE_CACHE_SIZE:
                cache.popitem(last=False)
        cache.move_to_end(digest)
        return cache[digest]

    def _select_batch(self, rows, scores, k: int, subject: str, best: list, cache: OrderedDict) -> None:
        keys = np.empty(len(rows), dtype=[('score', '<f4'), ('id', '<i8')])
        keys['score'] = scores
        keys['id'] = np.fromiter((row[0] for row in rows), dtype=np.int64, count=len(rows))
        count = min(max(k * 2, 32), len(rows))
        seen = set()
        while True:
            selected = np.argpartition(keys, len(rows) - count, order=['score', 'id'])[-count:]
            selected = selected[np.argsort(keys[selected], order=['score', 'id'])][::-1]
            for index in selected:
                if index in seen:
                    continue
                seen.add(index)
                item = (float(scores[index]), rows[index][0])
                if len(best) >= k and item <= best[0]:
                    break
                if self._source_available(rows[index][1], subject, cache):
                    heapq.heappush(best, item)
                    if len(best) > k:
                        heapq.heappop(best)
            last = selected[-1]
            threshold = (float(scores[last]), rows[last][0])
            if count == len(rows) or (len(best) >= k and threshold <= best[0]):
                return
            count = min(count * 2, len(rows))

    def _query_vector(self, query: str, k: int):
        if not query.strip() or not 1 <= k <= MAX_SEARCH_RESULTS:
            raise ValueError(f'Enter a query and a result count from 1 to {MAX_SEARCH_RESULTS}.')
        profile = self.repository.profile()
        blob = vector_blob(self.embedder.embed([profile.query(query)])[0])
        dimensions = int(self.repository.get('dimensions') or 0)
        vector = np.frombuffer(blob, dtype='<f4')
        if len(vector) != dimensions:
            raise ValueError('Query dimensions differ from this index, or index has no chunks.')
        return vector

    def search(self, query: str, k: int, subject: str = '') -> list[dict]:
        vector = self._query_vector(query, k)
        best = self.backend.search(vector, k, subject, self._exact) if self.backend else self._exact(vector, k, subject)
        return self._hits(best, subject)

    def search_documents(self, query: str, k: int, subject: str = '', candidate_depth: int = 4096) -> list[dict]:
        if not k <= candidate_depth <= 1000000:
            raise ValueError('Document candidate depth must be between K and 1000000.')
        vector = self._query_vector(query, k)
        if self.backend:
            best = self.backend.search_documents(vector, k, subject, self._exact_documents, candidate_depth)
            diagnostics = dict(self.backend.last_search)
            if diagnostics['exact_fallback']:
                diagnostics['ann_candidates_examined'] = diagnostics['candidate_chunks']
                diagnostics['candidate_chunks'] = self.last_search['candidate_chunks']
            self.last_search = diagnostics
        else:
            best = self._exact_documents(vector, k, subject)
        return self._hits(best, subject)

    def _hits(self, best, subject: str) -> list[dict]:
        result = []
        for score, identifier in sorted(best, reverse=True):
            digest, page, text = self.repository.content(identifier)
            sources = self.repository.sources(digest, subject)
            if sources:
                result.append({'chunk_id': identifier, 'document_id': digest, 'score': score, 'page': page, 'text': text, 'sources': sources})
        return result

    def _exact(self, vector, k: int, subject: str) -> list:
        dimensions = len(vector)
        batch_size = min(SEARCH_BATCH_SIZE, max(1, SEARCH_MATRIX_BYTES // (dimensions * 4)))
        best: list[tuple[float, int]] = []
        source_cache: OrderedDict[str, bool] = OrderedDict()
        cursor = self.repository.vectors(subject)
        while rows := cursor.fetchmany(batch_size):
            matrix = np.stack([np.frombuffer(row[2], dtype='<f4') for row in rows])
            self._select_batch(rows, matrix @ vector, k, subject, best, source_cache)
        return best

    def _exact_documents(self, vector, k: int, subject: str) -> list:
        batch_size = min(SEARCH_BATCH_SIZE, max(1, SEARCH_MATRIX_BYTES // (len(vector) * 4)))
        ranking = DocumentRanking(k)
        cache: OrderedDict[str, bool] = OrderedDict()
        scanned = 0
        def available(digest):
            return self._source_available(digest, subject, cache)
        cursor = self.repository.vectors(subject)
        try:
            while rows := cursor.fetchmany(batch_size):
                matrix = np.stack([np.frombuffer(row[2], dtype='<f4') for row in rows])
                scores = matrix @ vector
                for row, score in zip(rows, scores):
                    ranking.offer(row[1], float(score), row[0], available)
                scanned += len(rows)
        finally:
            cursor.close()
        self.last_search = {'unit': 'document', 'method': 'exact', 'candidate_chunks': scanned,
                            'candidate_limit': None, 'exact_fallback': False}
        return ranking.results()
