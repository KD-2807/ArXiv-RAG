from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
from .domain import Profile
from .storage import Repository


@dataclass(frozen=True)
class Checkpoint:
    consumed: int
    prefix: str


class CheckpointMismatch(RuntimeError):
    pass


class CheckpointStore:
    def __init__(self, repository: Repository):
        self.repository = repository

    def load(self, digest: str, profile: Profile) -> Checkpoint:
        signature = 'checkpoint-v1:' + profile.json()
        with self.repository.transaction():
            row = self.repository.db.execute('SELECT profile,consumed,prefix FROM checkpoint_jobs WHERE hash=?', (digest,)).fetchone()
            if row is not None and row[0] == signature:
                return Checkpoint(row[1], row[2])
            self._delete(digest)
            prefix = hashlib.sha256().hexdigest()
            self.repository.db.execute('INSERT INTO checkpoint_jobs VALUES(?,?,0,?)', (digest, signature, prefix))
            return Checkpoint(0, prefix)

    def _delete(self, digest: str) -> None:
        self.repository.db.execute('DELETE FROM checkpoint_chunks WHERE hash=?', (digest,))
        self.repository.db.execute('DELETE FROM checkpoint_jobs WHERE hash=?', (digest,))

    def discard(self, digest: str) -> None:
        with self.repository.transaction():
            self._delete(digest)

    def save(self, digest: str, expected: int, consumed: int, prefix: str,
             values: Iterable[tuple[int, str, bytes]]) -> None:
        with self.repository.transaction():
            row = self.repository.db.execute('SELECT consumed FROM checkpoint_jobs WHERE hash=?', (digest,)).fetchone()
            if row is None or row[0] != expected or consumed <= expected:
                raise CheckpointMismatch('Checkpoint changed while embedding a batch.')
            ordinal = self.repository.db.execute('SELECT COUNT(*) FROM checkpoint_chunks WHERE hash=?', (digest,)).fetchone()[0]
            for page, text, vector in values:
                self.repository.check_dimensions(vector)
                self.repository.db.execute('INSERT INTO checkpoint_chunks VALUES(?,?,?,?,?)', (digest, ordinal, page, text, vector))
                ordinal += 1
            self.repository.db.execute('UPDATE checkpoint_jobs SET consumed=?,prefix=? WHERE hash=?', (consumed, prefix, digest))

    def count(self, digest: str) -> int:
        return self.repository.db.execute('SELECT COUNT(*) FROM checkpoint_chunks WHERE hash=?', (digest,)).fetchone()[0]

    def publish(self, digest: str, path, subject: str) -> None:
        with self.repository.transaction():
            if not self.repository.has(digest):
                self.repository.db.execute('DELETE FROM chunks WHERE hash=?', (digest,))
                self.repository.db.execute('''INSERT INTO chunks(hash,page,text,vector)
                    SELECT hash,page,text,vector FROM checkpoint_chunks WHERE hash=? ORDER BY ordinal''', (digest,))
                self.repository.finish(digest)
            self.repository.attach(path, digest, subject)
            self._delete(digest)

    def stats(self) -> dict[str, int]:
        return {'pending_documents': self.repository.db.execute('SELECT COUNT(*) FROM checkpoint_jobs').fetchone()[0],
                'pending_chunks': self.repository.db.execute('SELECT COUNT(*) FROM checkpoint_chunks').fetchone()[0]}

    def reset(self) -> dict[str, int]:
        with self.repository.transaction():
            counts = self.stats()
            self.repository.db.execute('DELETE FROM checkpoint_chunks')
            self.repository.db.execute('DELETE FROM checkpoint_jobs')
            return counts
