from pathlib import Path
import hashlib
import json
from .limits import PIPELINE_VERSION
from .filesystem import root_identity


class CleanupService:
    def __init__(self, repository):
        self.repository = repository

    def validate_root(self) -> tuple[Path, str]:
        if self.repository.profile().version not in (2, PIPELINE_VERSION):
            raise ValueError('Cleanup requires a pipeline-v2 index. Keep the legacy index for searching and create a new v2 database.')
        if self.repository.get('pending_root') is not None:
            raise ValueError('Cleanup is blocked during pending relocation. Resume it or run cancel-relocation.')
        if self.repository.get('scan_complete') != '1' or self.repository.get('root_identity') is None:
            raise ValueError('Cleanup requires a completed scan in this version. Re-index the root before cleanup.')
        root = Path(self.repository.get('root'))
        try:
            identity = root_identity(root)
        except OSError as error:
            raise ValueError('Cleanup refused: PDF root is unavailable. Restore its directory or mount first.') from error
        if identity != self.repository.get('root_identity'):
            raise ValueError('Cleanup refused: PDF root filesystem identity changed. Restore the original mount.')
        snapshot = self.repository.get('mounted_sources')
        if snapshot is None:
            raise ValueError('Cleanup requires a mounted-source baseline. Restore all storage and index with --accept-source-baseline.')
        self.repository.mounted_sources.validate(root, snapshot)
        return root, identity

    def _missing(self, path: str) -> bool:
        try:
            Path(path).stat()
            return False
        except FileNotFoundError:
            return True
        except OSError as error:
            raise ValueError(f'Cleanup refused: cannot inspect {path}: {error}') from error

    def plan(self) -> dict:
        root, identity = self.validate_root()
        self.repository.db.execute('CREATE TEMP TABLE IF NOT EXISTS prune_files(path TEXT PRIMARY KEY)')
        self.repository.db.execute('DELETE FROM prune_files')
        self.repository.db.execute('CREATE TEMP TABLE IF NOT EXISTS prune_errors(path TEXT PRIMARY KEY)')
        self.repository.db.execute('DELETE FROM prune_errors')
        self.repository.db.execute('CREATE TEMP TABLE IF NOT EXISTS prune_hashes(hash TEXT PRIMARY KEY)')
        self.repository.db.execute('DELETE FROM prune_hashes')
        for (path,) in self.repository.db.execute('SELECT path FROM files'):
            if self._missing(path):
                self.repository.db.execute('INSERT INTO prune_files VALUES(?)', (path,))
        for path, kind in self.repository.db.execute('SELECT path,kind FROM errors'):
            diagnostic = kind == 'diagnostic' or path.startswith(('scan:', 'non-UTF8:'))
            if diagnostic or not Path(path).is_relative_to(root) or self._missing(path):
                self.repository.db.execute('INSERT INTO prune_errors VALUES(?)', (path,))
        self.repository.db.execute('''INSERT INTO prune_hashes
            SELECT hash FROM (SELECT hash FROM documents UNION SELECT hash FROM chunks) d
            WHERE NOT EXISTS(SELECT 1 FROM files f WHERE f.hash=d.hash
                AND NOT EXISTS(SELECT 1 FROM prune_files p WHERE p.path=f.path))''')
        queries = {
            'files': 'SELECT COUNT(*) FROM prune_files',
            'errors': 'SELECT COUNT(*) FROM prune_errors',
            'documents': 'SELECT COUNT(*) FROM documents WHERE hash IN (SELECT hash FROM prune_hashes)',
            'chunks': 'SELECT COUNT(*) FROM chunks WHERE hash IN (SELECT hash FROM prune_hashes)',
        }
        counts = {key: self.repository.db.execute(query).fetchone()[0] for key, query in queries.items()}
        fingerprint = hashlib.sha256()
        fingerprint.update(json.dumps([str(self.repository.path), str(root), identity, self.repository.get('mounted_sources'), self.repository.get('profile'), counts], sort_keys=True).encode())
        for query in (
            'SELECT path FROM prune_files ORDER BY path',
            'SELECT path FROM prune_errors ORDER BY path',
            'SELECT hash FROM prune_hashes ORDER BY hash',
            'SELECT id,hash,page,length(text),length(vector) FROM chunks WHERE hash IN (SELECT hash FROM prune_hashes) ORDER BY id',
        ):
            for row in self.repository.db.execute(query):
                fingerprint.update(json.dumps(row).encode())
                fingerprint.update(b'\n')
        self.validate_root()
        if root_identity(root) != identity:
            raise ValueError('Cleanup refused: root changed while preparing the preview.')
        return {'dry_run': True, 'root': str(root), **counts, 'token': fingerprint.hexdigest()}

    def prune(self, token: str | None = None) -> dict:
        with self.repository.transaction():
            plan = self.plan()
            if token is None:
                return plan
            if token != plan['token']:
                raise ValueError('Cleanup preview changed. Run prune --dry-run again and review the new counts.')
            self.validate_root()
            self.repository.db.execute('DELETE FROM files WHERE path IN (SELECT path FROM prune_files)')
            self.repository.db.execute('DELETE FROM errors WHERE path IN (SELECT path FROM prune_errors)')
            self.repository.db.execute('DELETE FROM chunks WHERE hash IN (SELECT hash FROM prune_hashes)')
            self.repository.db.execute('DELETE FROM documents WHERE hash IN (SELECT hash FROM prune_hashes)')
            self.validate_root()
            for (path,) in self.repository.db.execute('SELECT path FROM prune_files'):
                if not self._missing(path):
                    raise ValueError('A source returned during cleanup. No deletions were committed; preview again.')
            return {**plan, 'dry_run': False}
