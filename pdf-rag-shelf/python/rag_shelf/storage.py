from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3
from .domain import Profile
from .limits import PIPELINE_VERSION
from .filesystem import MountedSources
from .root_manager import RootManager
from .cleanup import CleanupService

SCHEMA = '''
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(hash TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, hash TEXT NOT NULL, subject TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS files_hash ON files(hash);
CREATE TABLE IF NOT EXISTS chunks(id INTEGER PRIMARY KEY, hash TEXT NOT NULL, page INTEGER NOT NULL, text TEXT NOT NULL, vector BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS chunks_hash ON chunks(hash);
CREATE TABLE IF NOT EXISTS errors(path TEXT PRIMARY KEY, message TEXT NOT NULL, hash TEXT, kind TEXT NOT NULL DEFAULT 'unknown');
CREATE TABLE IF NOT EXISTS staged_files(path TEXT PRIMARY KEY, hash TEXT NOT NULL, subject TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoint_jobs(hash TEXT PRIMARY KEY, profile TEXT NOT NULL, consumed INTEGER NOT NULL, prefix TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoint_chunks(hash TEXT NOT NULL, ordinal INTEGER NOT NULL, page INTEGER NOT NULL, text TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(hash,ordinal));
'''
STAT_QUERIES = {
    'documents': 'SELECT COUNT(*) FROM documents',
    'files': 'SELECT COUNT(*) FROM files',
    'chunks': 'SELECT COUNT(*) FROM chunks',
    'errors': 'SELECT COUNT(*) FROM errors',
}

def private_directory(path: Path) -> None:
    if not path.exists():
        private_directory(path.parent)
        path.mkdir(mode=0o700, exist_ok=True)

class Repository:
    def __init__(self, path, mounted_sources=None):
        self.path = Path(path).expanduser().resolve()
        private_directory(self.path.parent)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        self.db = sqlite3.connect(self.path, timeout=5)
        self._closed = False
        try:
            self.db.executescript(SCHEMA)
            if 'hash' not in {row[1] for row in self.db.execute('PRAGMA table_info(errors)')}:
                self.db.execute('ALTER TABLE errors ADD COLUMN hash TEXT')
                self.db.commit()
            if 'kind' not in {row[1] for row in self.db.execute('PRAGMA table_info(errors)')}:
                self.db.execute("ALTER TABLE errors ADD COLUMN kind TEXT NOT NULL DEFAULT 'unknown'")
                self.db.commit()
            for table in ('files', 'chunks'):
                for action in ('INSERT', 'UPDATE', 'DELETE'):
                    self.db.execute(f'''CREATE TRIGGER IF NOT EXISTS revision_{table}_{action}
                        AFTER {action} ON {table} BEGIN
                        INSERT INTO meta(key,value) VALUES('search_revision','1')
                        ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1; END''')
            self.db.execute('PRAGMA cache_size=-4096')
            self.db.execute('PRAGMA temp_store=FILE')
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            for suffix in ('-wal', '-shm'):
                sidecar = Path(str(self.path) + suffix)
                if sidecar.exists():
                    sidecar.chmod(0o600)
        except BaseException:
            self.close()
            raise
        self._dimensions: int | None = None
        self.roots = RootManager(self)
        self.cleanup = CleanupService(self)
        self.mounted_sources = mounted_sources or MountedSources()

    @contextmanager
    def transaction(self):
        dimensions = self._dimensions
        self.db.execute('BEGIN IMMEDIATE')
        try:
            with self.db:
                yield
        except BaseException:
            self._dimensions = dimensions
            raise

    def finish(self, digest):
        self.db.execute('INSERT INTO documents VALUES(?)', (digest,))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        if not self._closed:
            try:
                self.db.close()
            finally:
                self._closed = True

    def get(self, key):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, value))

    def profile(self):
        if self.get('variant') != 'python':
            raise ValueError('Choose a Python index.')
        value = self.get('profile')
        if value is None:
            raise ValueError('Index is empty. Index a PDF folder first.')
        profile = Profile(**json.loads(value))
        if profile.version not in (1, 2, PIPELINE_VERSION):
            raise ValueError('Unsupported index pipeline version.')
        return profile

    def has(self, digest):
        return self.db.execute('SELECT 1 FROM documents WHERE hash=?', (digest,)).fetchone() is not None

    def attach(self, path, digest, subject):
        table = 'staged_files' if self.roots.staging else 'files'
        query = f'''INSERT INTO {table} VALUES(?,?,?) ON CONFLICT(path) DO UPDATE
            SET hash=excluded.hash,subject=excluded.subject
            WHERE hash!=excluded.hash OR subject!=excluded.subject''' 
        self.db.execute(query, (str(path), digest, subject))
        self.db.execute('DELETE FROM errors WHERE path=?', (str(path),))

    def invalidate(self, path):
        query = 'DELETE FROM staged_files WHERE path=?' if self.roots.staging else 'DELETE FROM files WHERE path=?'
        self.db.execute(query, (str(path),))

    def error(self, path, message, digest=None, kind='unknown'):
        self.db.execute('INSERT OR REPLACE INTO errors(path,message,hash,kind) VALUES(?,?,?,?)', (str(path), message, digest, kind))

    def failed(self, path, digest):
        return self.db.execute("SELECT 1 FROM errors WHERE path=? AND hash=? AND kind='deterministic'", (str(path), digest)).fetchone() is not None

    def check_dimensions(self, vector):
        if not vector or len(vector) % 4:
            raise ValueError('Embedding bytes must contain complete float32 values.')
        dim = len(vector) // 4
        if self._dimensions is None:
            stored = self.get('dimensions')
            self._dimensions = int(stored) if stored is not None else dim
            if stored is None:
                self.db.execute('INSERT INTO meta VALUES(?,?)', ('dimensions', str(dim)))
        if self._dimensions != dim:
            raise ValueError('Embedding dimensions changed. Create a new index.')

    def add(self, digest, page, text, vector):
        self.check_dimensions(vector)
        self.db.execute('INSERT INTO chunks(hash,page,text,vector) VALUES(?,?,?,?)', (digest, page, text, vector))

    def sources(self, digest, subject):
        rows = self.db.execute('SELECT path,subject FROM files WHERE hash=? ORDER BY path', (digest,))
        return [{'path': path, 'subject': category} for path, category in rows
                if (not subject or subject == category) and Path(path).is_file()]

    def vectors(self, subject):
        return self.db.execute('''SELECT id,hash,vector FROM chunks c
            WHERE EXISTS(SELECT 1 FROM files f WHERE f.hash=c.hash AND (?='' OR f.subject=?))''', (subject, subject))

    def content(self, identifier):
        return self.db.execute('SELECT hash,page,text FROM chunks WHERE id=?', (identifier,)).fetchone()

    def stats(self):
        return {name: self.db.execute(query).fetchone()[0] for name, query in STAT_QUERIES.items()}


    def bind(self, profile: Profile, root: Path, relocate: bool = False, accept_source_baseline: bool = False) -> bool:
        return self.roots.bind(profile, root, relocate, accept_source_baseline)

    def finish_scan(self, root: Path, identity: str) -> None:
        self.roots.finish_scan(root, identity)

    def cancel_relocation(self) -> None:
        self.roots.cancel_relocation()

    def prune(self, token: str | None = None) -> dict:
        return self.cleanup.prune(token)
