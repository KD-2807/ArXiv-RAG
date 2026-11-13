from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlparse
import json
import os
import re

SEARCH_FIELDS = {'all': 'all', 'title': 'ti', 'abstract': 'abs', 'author': 'au', 'comments': 'co', 'journal_ref': 'jr', 'report_num': 'rn'}


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    query: str
    folder: str
    monthly: bool


@dataclass(frozen=True)
class Paper:
    identifier: str
    versioned_id: str
    title: str
    published: str
    categories: tuple[str, ...]

    @classmethod
    def create(cls, identifier, title, published, categories):
        if not re.fullmatch(r'(?:\d{4}\.\d{4,5}|[A-Za-z-]+(?:\.[A-Z]{2})?/\d{7})v\d+', identifier):
            raise ValueError(f'Invalid arXiv identifier: {identifier}')
        return cls(re.sub(r'v\d+$', '', identifier), identifier, ' '.join(title.split()), published, tuple(categories))

    @property
    def filename(self):
        return self.identifier.replace('/', '_') + '.pdf'


def check_folder(folder):
    if not folder or len(folder) > 80 or any(not (c.isalnum() or c in ' _-') for c in folder):
        raise ValueError('Folder names may contain letters, numbers, spaces, _ and - (max 80 characters).')


def keyword_query(text, field):
    text = ' '.join(text.split())
    if not text or len(text) > 200 or not re.fullmatch(r'[\w .\'"-]+', text):
        raise ValueError('Keywords may contain letters, numbers, spaces, quotes, . - and \'.')
    terms = [phrase or word for phrase, word in re.findall(r'"([^"]+)"|(\S+)', text)]
    clauses = [f'{field}:{term}' if term.isalnum() else f'{field}:"{term.replace(chr(34), "")}"' for term in terms]
    if not clauses:
        raise ValueError('Keywords must not be empty.')
    return clauses[0] if len(clauses) == 1 else '(' + ' AND '.join(clauses) + ')'


def parse_keyword_line(line):
    if line.startswith(('http://', 'https://')):
        address, _, folder = line.partition(' ')
        parsed = urlparse(address)
        if parsed.hostname not in ('arxiv.org', 'www.arxiv.org') or not parsed.path.rstrip('/').endswith('/search'):
            raise ValueError('Paste an arxiv.org/search URL or use keyword=Folder.')
        parameters = parse_qs(parsed.query)
        text = parameters.get('query', [''])[0]
        searchtype = parameters.get('searchtype', ['all'])[0]
        if searchtype not in SEARCH_FIELDS:
            raise ValueError(f'Unsupported search type: {searchtype}.')
        folder = folder.strip() or ''.join(c for c in text if c.isalnum() or c in ' _-').strip()[:80]
        return text, SEARCH_FIELDS[searchtype], folder
    text, separator, folder = line.rpartition('=')
    if not separator:
        raise ValueError('Use one keyword=Folder per line, for example connectome=Connectome.')
    return text.strip(), 'all', folder.strip()


@dataclass
class Settings:
    root: str
    subjects: str = 'cs.SD=Sound\ncs.RO=Robotics'
    mode: str = 'limited'
    limit: int = 100
    contact: str = ''
    keywords: str = ''

    def sources(self):
        result = []
        for line in self.subjects.splitlines():
            if not line.strip():
                continue
            category, separator, folder = line.strip().partition('=')
            category, folder = category.strip(), folder.strip()
            if not separator or not re.fullmatch(r'[a-z][a-z0-9-]*(?:\.[A-Z]{2})?', category):
                raise ValueError('Use one exact category=Folder per line, for example cs.SD=Sound.')
            check_folder(folder)
            result.append(Source(category, category, f'cat:{category}', folder, True))
        for line in self.keywords.splitlines():
            if not line.strip():
                continue
            text, field, folder = parse_keyword_line(line.strip())
            check_folder(folder)
            query = keyword_query(text, field)
            result.append(Source('kw:' + query, ' '.join(text.split()), query, folder, False))
        if not result:
            raise ValueError('Add at least one category or keyword.')
        keys = [source.key for source in result]
        for key in keys:
            if keys.count(key) > 1:
                raise ValueError(f'Duplicate entry: {key}')
        if self.mode not in ('limited', 'daily', 'all') or not 1 <= self.limit <= 1000000:
            raise ValueError('Choose a valid mode and a count from 1 to 1000000.')
        if not Path(self.root).is_absolute():
            raise ValueError('Choose an absolute download directory.')
        if any(ord(c) < 32 for c in self.contact) or len(self.contact) > 200:
            raise ValueError('Contact must be one line, at most 200 characters.')
        return result

    @classmethod
    def load(cls, path):
        if path.exists():
            return cls(**json.loads(path.read_text()))
        return cls(str(Path.home() / 'Downloads' / 'ArxivShelf-Python'))

    def save(self, path):
        self.sources()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(asdict(self), indent=2))
        temporary.replace(path)


class Catalog(Protocol):
    def page(self, query: str, start: datetime, end: datetime, offset: int) -> tuple[int, list[Paper]]: ...
    def download(self, paper: Paper, target: Path) -> None: ...


def state_directory():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'arxiv-shelf-python'


def utcnow():
    return datetime.now(timezone.utc).replace(second=0, microsecond=0)
