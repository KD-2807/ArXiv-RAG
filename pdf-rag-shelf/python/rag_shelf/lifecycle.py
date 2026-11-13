from contextlib import contextmanager
from collections.abc import Iterator
from typing import TypeVar

T = TypeVar('T')


@contextmanager
def closing_iterator(iterator: Iterator[T]) -> Iterator[Iterator[T]]:
    try:
        yield iterator
    finally:
        close = getattr(iterator, 'close', None)
        if close is not None:
            close()
