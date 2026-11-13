from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import os
import shutil
import sqlite3
from typing import Protocol


class PaperStore(Protocol):
    def path(self, paper): ...
    def contains(self, paper): ...
    def record(self, paper): ...
    def link(self, paper, folder): ...
    def checkpoint(self, category): ...
    def complete(self, category, timestamp): ...


def valid_pdf(path):
    try:
        with path.open('rb') as stream:
            if stream.read(5) != b'%PDF-':
                return False
            stream.seek(max(0, path.stat().st_size - 2048))
            return b'%%EOF' in stream.read()
    except OSError:
        return False


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Repository:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        marker = self.root / '.variant'
        if marker.exists() and marker.read_text() != 'python':
            raise ValueError('This directory belongs to the Rust version. Choose a separate directory.')
        marker.write_text('python')
        self.objects = self.root / '.objects'
        self.objects.mkdir(exist_ok=True)
        if self.objects.is_symlink():
            raise ValueError('The .objects directory must not be a symbolic link.')
        self.db = sqlite3.connect(self.root / 'catalog.sqlite3')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY, metadata TEXT NOT NULL, sha256 TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS checkpoints(category TEXT PRIMARY KEY, timestamp TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    def path(self, paper):
        return self.objects / paper.filename

    def contains(self, paper):
        path = self.path(paper)
        if not valid_pdf(path):
            return False
        row = self.db.execute('SELECT sha256 FROM papers WHERE id=?', (paper.identifier,)).fetchone()
        if row is None:
            self.record(paper)
            return True
        return digest(path) == row[0]

    def record(self, paper):
        path = self.path(paper)
        if not valid_pdf(path):
            raise ValueError('Downloaded file is not a complete PDF.')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO papers VALUES(?,?,?)',
                            (paper.identifier, json.dumps(asdict(paper)), digest(path)))

    def link(self, paper, folder):
        directory = self.root / folder
        directory.mkdir(exist_ok=True)
        if directory.is_symlink() or directory.resolve().parent != self.root:
            raise ValueError('Subject folder must be inside the download directory.')
        source, target = self.path(paper), directory / paper.filename
        if target.exists() and not target.is_symlink() and os.path.samefile(source, target):
            return
        temporary = target.with_suffix('.link.tmp')
        temporary.unlink(missing_ok=True)
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copyfile(source, temporary)
        temporary.replace(target)

    def checkpoint(self, category):
        row = self.db.execute('SELECT timestamp FROM checkpoints WHERE category=?', (category,)).fetchone()
        return row[0] if row else None

    def complete(self, category, timestamp):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?)', (category, timestamp))
