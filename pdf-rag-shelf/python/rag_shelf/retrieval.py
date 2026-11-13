from pathlib import Path
from typing import Any
import hashlib
import json
import os
import stat
import tempfile
import uuid
import numpy as np
from .domain import ConfigurationError, checksum
from .storage import private_directory
from .ranking import DocumentRanking

ANN_LINKS = 16
ANN_EF_SEARCH = 96
ANN_CANDIDATE_LIMIT = 4096
ANN_BATCH_SIZE = 512
DEFAULT_ANN_MEMORY_MIB = 512


class FaissBackend:
    def __init__(self, repository, memory_mib: int = DEFAULT_ANN_MEMORY_MIB, search_effort: int = ANN_EF_SEARCH):
        if not 16 <= memory_mib <= 32768:
            raise ValueError('ANN memory budget must be 16–32768 MiB.')
        if not 16 <= search_effort <= 65536:
            raise ValueError('ANN search effort must be 16–65536.')
        self.search_effort = search_effort
        self.repository = repository
        self.memory_bytes = memory_mib * 1024 * 1024
        self.directory = Path(str(repository.path) + '.ann')
        self._loaded: tuple[dict, Any, np.ndarray] | None = None
        self.last_search: dict[str, object] = {}

    @staticmethod
    def _faiss():
        try:
            import faiss
        except ImportError as error:
            raise ConfigurationError('Optional FAISS dependency is missing. Install python/requirements-ann.txt.') from error
        faiss.omp_set_num_threads(2)
        return faiss

    @staticmethod
    def _key(subject: str) -> str:
        return 'ann:' + hashlib.sha256(subject.encode()).hexdigest()

    def _signature(self, subject: str) -> dict:
        return {'format': 1, 'profile': self.repository.get('profile'),
                'revision': self.repository.get('search_revision') or '0',
                'dimensions': int(self.repository.get('dimensions') or 0), 'subject': subject}

    def _budget(self, count: int, dimensions: int) -> int:
        estimate = count * (dimensions * 4 + ANN_LINKS * 8 + 128) * 2 + 16 * 1024 * 1024
        if estimate > self.memory_bytes:
            raise ValueError(f'ANN estimated working memory {estimate // (1024 * 1024)} MiB exceeds the configured budget. Use exact search or raise --ann-memory-mib.')
        return estimate

    def build(self, subject: str = '') -> dict:
        faiss = self._faiss()
        signature = self._signature(subject)
        count = self.repository.db.execute('''SELECT COUNT(*) FROM chunks c WHERE EXISTS(
            SELECT 1 FROM files f WHERE f.hash=c.hash AND (?='' OR f.subject=?))''', (subject, subject)).fetchone()[0]
        if count == 0 or not signature['dimensions']:
            raise ValueError('No indexed chunks match this subject.')
        estimate = self._budget(count, signature['dimensions'])
        private_directory(self.directory)
        self.directory.chmod(0o700)
        index = faiss.IndexHNSWFlat(signature['dimensions'], ANN_LINKS, faiss.METRIC_INNER_PRODUCT)
        index.hnsw.efConstruction = 80
        index.hnsw.efSearch = self.search_effort
        ids = np.empty(count, dtype=np.int64)
        cursor, offset = self.repository.vectors(subject), 0
        while rows := cursor.fetchmany(ANN_BATCH_SIZE):
            matrix = np.stack([np.frombuffer(row[2], dtype='<f4') for row in rows])
            index.add(matrix)
            ids[offset:offset + len(rows)] = [row[0] for row in rows]
            offset += len(rows)
        if offset != count or signature != self._signature(subject):
            raise ValueError('Database changed while building ANN. Retry with the index idle.')
        generation = uuid.uuid4().hex
        paths = [self.directory / (generation + suffix) for suffix in ('.faiss', '.npy')]
        old = self.repository.get(self._key(subject))
        committed = False
        try:
            for path in paths:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            faiss.write_index(index, str(paths[0]))
            with paths[1].open('wb') as stream:
                np.save(stream, ids, allow_pickle=False)
                stream.flush()
                os.fsync(stream.fileno())
            with paths[0].open('rb') as stream:
                os.fsync(stream.fileno())
            record = {'signature': signature, 'count': count, 'estimated_bytes': estimate,
                      'files': [{'name': path.name, 'sha256': checksum(path), 'bytes': path.stat().st_size} for path in paths]}
            with self.repository.transaction():
                if signature != self._signature(subject):
                    raise ValueError('Database changed before ANN publication.')
                self.repository.set(self._key(subject), json.dumps(record, sort_keys=True))
            committed = True
        finally:
            if not committed:
                for path in paths:
                    path.unlink(missing_ok=True)
        if old:
            for item in json.loads(old).get('files', []):
                name = item['name']
                if Path(name).name == name:
                    (self.directory / name).unlink(missing_ok=True)
        self._loaded = None
        return {'backend': 'faiss-hnsw', 'chunks': count, 'subject': subject, 'estimated_bytes': estimate}

    def _load(self, subject: str):
        signature = self._signature(subject)
        if self._loaded is not None and self._loaded[0] == signature:
            return self._loaded[1:]
        value = self.repository.get(self._key(subject))
        if value is None:
            raise ValueError('No ANN cache for this subject. Run build-search-index with the same --subject, or use --backend exact.')
        record = json.loads(value)
        if record['signature'] != signature:
            raise ValueError('ANN cache is stale. Run build-search-index again, or use --backend exact.')
        self._budget(record['count'], signature['dimensions'])
        faiss = self._faiss()
        with tempfile.TemporaryDirectory(prefix='rag-ann-') as directory:
            copies = []
            for number, item in enumerate(record['files']):
                if Path(item['name']).name != item['name']:
                    raise ValueError('Invalid ANN cache filename.')
                path = self.directory / item['name']
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, 'rb') as stream:
                    details = os.fstat(stream.fileno())
                    if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid() or details.st_mode & 0o022:
                        raise ValueError('ANN cache ownership or permissions are unsafe. Rebuild it.')
                    if details.st_size != item['bytes'] or details.st_size > self.memory_bytes:
                        raise ValueError('ANN cache size is invalid. Rebuild it.')
                    target = Path(directory) / str(number)
                    digest = hashlib.sha256()
                    with target.open('wb') as output:
                        for block in iter(lambda: stream.read(1024 * 1024), b''):
                            digest.update(block)
                            output.write(block)
                    if digest.hexdigest() != item['sha256']:
                        raise ValueError('ANN cache integrity check failed. Rebuild it.')
                    copies.append(target)
            index = faiss.read_index(str(copies[0]))
            ids = np.load(copies[1], allow_pickle=False)
        if index.d != signature['dimensions'] or index.ntotal != record['count'] or ids.dtype != np.int64 or ids.shape != (index.ntotal,):
            raise ValueError('ANN cache metadata mismatch. Rebuild it.')
        index.hnsw.efSearch = self.search_effort
        self._loaded = (signature, index, ids)
        return index, ids

    def search(self, vector, k: int, subject: str, exact):
        index, identifiers = self._load(subject)
        count = min(max(64, k * 8), index.ntotal)
        limit = min(max(ANN_CANDIDATE_LIMIT, count), index.ntotal)
        while True:
            _, labels = index.search(vector.reshape(1, -1), count)
            best, seen, available = [], set(), {}
            for label in labels[0]:
                if label < 0:
                    continue
                identifier = int(identifiers[label])
                if identifier in seen:
                    continue
                seen.add(identifier)
                row = self.repository.db.execute('SELECT hash,vector FROM chunks WHERE id=?', (identifier,)).fetchone()
                if row is None:
                    continue
                digest, blob = row
                if digest not in available:
                    available[digest] = bool(self.repository.sources(digest, subject))
                if available[digest]:
                    best.append((float(np.frombuffer(blob, dtype='<f4') @ vector), identifier))
            if len(best) >= k:
                return sorted(best, reverse=True)[:k]
            if count >= limit:
                return exact(vector, k, subject)
            count = min(count * 2, limit)

    def search_documents(self, vector, k: int, subject: str, exact, candidate_depth: int):
        index, identifiers = self._load(subject)
        limit = min(candidate_depth, index.ntotal)
        count = min(max(64, k * 8), limit)
        seen, available = set(), {}
        ranking = DocumentRanking(k)
        def source_exists(digest):
            if digest not in available:
                available[digest] = bool(self.repository.sources(digest, subject))
            return available[digest]
        while True:
            _, labels = index.search(vector.reshape(1, -1), count)
            for label in labels[0]:
                if label < 0:
                    continue
                identifier = int(identifiers[label])
                if identifier in seen:
                    continue
                seen.add(identifier)
                row = self.repository.db.execute('SELECT hash,vector FROM chunks WHERE id=?', (identifier,)).fetchone()
                if row is not None:
                    ranking.offer(row[0], float(np.frombuffer(row[1], dtype='<f4') @ vector), identifier, source_exists)
            if len(ranking.items) >= k:
                self.last_search = {'unit': 'document', 'method': 'faiss', 'candidate_chunks': len(seen),
                                    'candidate_limit': candidate_depth, 'exact_fallback': False}
                return ranking.results()
            if count >= limit:
                result = exact(vector, k, subject)
                self.last_search = {'unit': 'document', 'method': 'exact', 'candidate_chunks': len(seen),
                                    'candidate_limit': candidate_depth, 'exact_fallback': True}
                return result
            count = min(count * 2, limit)
