from collections.abc import Callable


class DocumentRanking:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.items: dict[str, tuple[float, int]] = {}
        self.floor: tuple[float, int] | None = None

    def offer(self, digest: str, score: float, identifier: int, available: Callable[[str], bool]) -> None:
        item = (score, identifier)
        previous = self.items.get(digest)
        if previous is not None and item <= previous:
            return
        if self.floor is not None and item <= self.floor:
            return
        if previous is None and not available(digest):
            return
        self.items[digest] = item
        if len(self.items) > self.limit:
            lowest = min(self.items, key=lambda key: self.items[key])
            del self.items[lowest]
        if len(self.items) == self.limit:
            self.floor = min(self.items.values())

    def results(self) -> list[tuple[float, int]]:
        return sorted(self.items.values(), reverse=True)
