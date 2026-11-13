from dataclasses import dataclass, asdict
from typing import Protocol
from pathlib import Path
from collections.abc import Iterator, Sequence
import hashlib
import json
import math
import struct
from .limits import PIPELINE_VERSION


@dataclass(frozen=True)
class Profile:
    model: str
    digest: str
    chunk: int = 600
    overlap: int = 80
    version: int = PIPELINE_VERSION
    ocr_language: str = ''

    def validate(self) -> None:
        if self.version != PIPELINE_VERSION:
            raise ValueError(f'Indexing requires pipeline v{PIPELINE_VERSION}. Create a new database; legacy indexes remain searchable.')
        if not 100 <= self.chunk <= 6000 or not 0 <= self.overlap < self.chunk // 2:
            raise ValueError('Chunk must be 100–6000 characters; overlap must be less than half the chunk.')

    def document(self, text: str) -> str:
        return 'search_document: ' + text if 'nomic-embed-text' in self.model.lower() else text

    def query(self, text: str) -> str:
        if 'nomic-embed-text' in self.model.lower():
            return 'search_query: ' + text
        if 'qwen3-embedding' in self.model.lower():
            return 'Instruct: Given a question, retrieve relevant passages from research papers.\nQuery: ' + text
        return text

    def json(self) -> str:
        values = asdict(self)
        if not self.ocr_language:
            values.pop('ocr_language')
        return json.dumps(values, sort_keys=True)


class DocumentError(RuntimeError):
    pass


class ContextLengthError(DocumentError):
    pass


class OllamaUnavailableError(RuntimeError):
    pass


class ConfigurationError(RuntimeError):
    pass


class CircuitOpenError(RuntimeError):
    pass


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class Extractor(Protocol):
    def pages(self, path: Path) -> Iterator[tuple[int, str]]: ...


def chunks(text: str, size: int, overlap: int) -> Iterator[str]:
    if size < 2 or not 0 <= overlap < size // 2:
        raise ValueError('Invalid chunk size or overlap.')
    text = ' '.join(text.split())
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind(' ', start + size // 2, end)
            if boundary > start:
                end = boundary
        value = text[start:end].strip()
        if value:
            yield value
        if end == len(text):
            break
        start = end - overlap


def checksum(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def vector_blob(values: Sequence[float]) -> bytes:
    if not values or not all(math.isfinite(x) for x in values):
        raise ValueError('Embedding contains missing or non-finite values.')
    norm = math.sqrt(sum(x * x for x in values))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('Embedding has an invalid norm.')
    return struct.pack('<' + 'f' * len(values), *(x / norm for x in values))
