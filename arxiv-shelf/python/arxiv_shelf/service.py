from datetime import datetime, timedelta, timezone
from pathlib import Path
from .domain import Catalog, Settings, utcnow
from .network import Cancelled
from .storage import PaperStore
from urllib.error import HTTPError
import fcntl
import os


class DownloadLock:
    def __enter__(self):
        directory = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'arxiv-shelf'
        directory.mkdir(parents=True, exist_ok=True)
        self.stream = (directory / 'download.lock').open('a+')
        try:
            fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.stream.close()
            raise RuntimeError('Another Python or Rust download job is running. Wait for it to finish.')
        return self

    def __exit__(self, *args):
        self.stream.close()


def windows(start, end):
    while end >= start:
        first = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        yield max(first, start), end
        end = first - timedelta(minutes=1)


class DownloadService:
    def __init__(self, catalog: Catalog, repository: PaperStore, stop, emit):
        self.catalog, self.repository, self.stop, self.emit = catalog, repository, stop, emit

    def papers(self, query, start, end):
        if self.stop.is_set():
            raise Cancelled('Stopped. Completed PDFs are kept.')
        total, page = self.catalog.page(query, start, end, 0)
        if total > 1000:
            minutes = int((end - start).total_seconds() // 60)
            if minutes == 0:
                raise RuntimeError('Too many results in one minute. Use arXiv bulk access.')
            middle = start + timedelta(minutes=minutes // 2)
            yield from self.papers(query, middle + timedelta(minutes=1), end)
            yield from self.papers(query, start, middle)
            return
        offset = 0
        while offset < total:
            if not page:
                raise RuntimeError('arXiv returned an incomplete page. Retry the job later.')
            yield from page
            offset += len(page)
            if offset < total:
                next_total, page = self.catalog.page(query, start, end, offset)
                if next_total != total:
                    raise RuntimeError('Search results changed during paging. Rerun to reconcile.')

    def run(self, settings: Settings):
        sources = settings.sources()
        snapshot = utcnow()
        downloaded = skipped = 0
        for source in sources:
            since = datetime(1991, 1, 1, tzinfo=timezone.utc)
            if settings.mode == 'daily':
                checkpoint = self.repository.checkpoint(source.key)
                since = (datetime.fromisoformat(checkpoint) if checkpoint else snapshot) - timedelta(days=7)
            count = 0
            self.emit(f'Scanning {source.label} → {source.folder}')
            ranges = windows(since, snapshot) if source.monthly else [(since, snapshot)]
            for start, end in ranges:
                for paper in self.papers(source.query, start, end):
                    if self.stop.is_set():
                        raise Cancelled('Stopped. Completed PDFs are kept.')
                    if self.repository.contains(paper):
                        skipped += 1
                    else:
                        self.emit(f'Downloading {paper.versioned_id}: {paper.title}')
                        try:
                            self.catalog.download(paper, self.repository.path(paper))
                        except HTTPError as error:
                            if error.code != 404:
                                raise
                            error.close()
                            self.emit(f'Unavailable PDF (404), skipped: {paper.versioned_id}')
                            continue
                        self.repository.record(paper)
                        downloaded += 1
                        count += 1
                    self.repository.link(paper, source.folder)
                    self.emit(f'Saved {downloaded} · skipped {skipped} · {source.label}')
                    if settings.mode == 'limited' and count >= settings.limit:
                        break
                if settings.mode == 'limited' and count >= settings.limit:
                    break
            if self.stop.is_set():
                raise Cancelled('Stopped. Completed PDFs are kept.')
            if settings.mode == 'daily':
                self.repository.complete(source.key, snapshot.isoformat())
        self.emit(f'Finished. Saved {downloaded}; skipped {skipped}.')
        return downloaded, skipped
