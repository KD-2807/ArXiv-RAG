from pathlib import Path
from .domain import Profile, checksum
from .filesystem import root_identity


class RootManager:
    def __init__(self, repository):
        self.repository = repository
        self.staging = False
        self.source_snapshot: str | None = None

    def bind(self, profile: Profile, root: Path, relocate: bool = False, accept_source_baseline: bool = False) -> bool:
        identity = root_identity(root)
        snapshot = self.repository.mounted_sources.capture(root)
        values = {'variant': 'python', 'profile': profile.json()}
        with self.repository.transaction():
            for key, value in values.items():
                old = self.repository.get(key)
                if old is not None and old != value:
                    raise ValueError(f'Index {key} differs. Use a new database for pipeline v3/model/settings. Existing indexes remain searchable.')
                self.repository.db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)', (key, value))
            old_root = self.repository.get('root')
            old_identity = self.repository.get('root_identity')
            moving = old_root is not None and (
                old_root != str(root) or (old_identity is not None and old_identity != identity)
                or self.repository.get('pending_root') is not None
                or (relocate and self.repository.get('mounted_sources') not in (None, snapshot)))
            if moving:
                if not relocate:
                    raise ValueError('Root changed or relocation is pending. Use --relocate-root to stage aliases, or cancel-relocation.')
                if self.repository.get('pending_root') == str(root) and self.repository.get('pending_sources'):
                    self.repository.mounted_sources.validate(root, self.repository.get('pending_sources'))
                self.repository.db.execute('DELETE FROM staged_files')
                self.repository.set('pending_root', str(root))
                self.repository.set('pending_sources', snapshot)
            else:
                previous = self.repository.get('mounted_sources')
                if previous is not None:
                    self.repository.mounted_sources.validate(root, previous)
                elif old_root is not None and not accept_source_baseline:
                    raise ValueError('This older index has no mounted-source baseline. Restore all PDF storage, then index with --accept-source-baseline once.')
                self.repository.set('mounted_sources', snapshot)
                self.repository.set('root', str(root))
                self.repository.set('scan_complete', '0')
                self.repository.db.execute("DELETE FROM errors WHERE kind='diagnostic'")
        self.staging = moving
        self.source_snapshot = snapshot
        return moving

    def finish_scan(self, root: Path, identity: str) -> None:
        snapshot = self.source_snapshot
        if snapshot is None:
            raise ValueError('Bind an indexing session before completing its scan.')
        with self.repository.transaction():
            self.repository.mounted_sources.validate(root, snapshot)
            if root_identity(root) != identity:
                raise ValueError('PDF root changed during indexing. Retry when the original filesystem is available.')
            if self.staging:
                missing = self.repository.db.execute('''SELECT COUNT(DISTINCT hash) FROM files f
                    WHERE NOT EXISTS(SELECT 1 FROM staged_files s WHERE s.hash=f.hash)''').fetchone()[0]
                if missing:
                    raise ValueError(f'Relocation incomplete: {missing} indexed documents have no matching copy in the new root. Original aliases are preserved.')
                for path, digest in self.repository.db.execute('SELECT path,hash FROM staged_files'):
                    if checksum(Path(path)) != digest:
                        raise ValueError('A staged PDF changed during relocation. Original aliases are preserved.')
                self.repository.db.execute('DELETE FROM files')
                self.repository.db.execute('INSERT INTO files SELECT * FROM staged_files')
                self.repository.db.execute('DELETE FROM staged_files')
                self.repository.db.execute('DELETE FROM errors')
                self.repository.db.execute("DELETE FROM meta WHERE key IN ('pending_root','pending_sources')")
                self.repository.set('root', str(root))
            if root_identity(root) != identity:
                raise ValueError('PDF root changed before scan completion.')
            self.repository.mounted_sources.validate(root, snapshot)
            self.repository.set('mounted_sources', snapshot)
            self.repository.set('root_identity', identity)
            self.repository.set('scan_complete', '1')
        self.staging = False

    def cancel_relocation(self) -> None:
        self.repository.profile()
        with self.repository.transaction():
            self.repository.db.execute('DELETE FROM staged_files')
            self.repository.db.execute("DELETE FROM meta WHERE key IN ('pending_root','pending_sources')")
        self.staging = False
