from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
import hashlib
import http.client
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from .domain import Paper
from .storage import valid_pdf


class Cancelled(Exception):
    pass


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in ('arxiv.org', 'export.arxiv.org', 'www.arxiv.org'):
            raise ValueError('Unexpected download redirect.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ArxivClient:
    def __init__(self, stop, cache, contact=''):
        self.stop = stop
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        for path in self.cache.glob('*.xml'):
            if time.time() - path.stat().st_mtime > 86400:
                path.unlink(missing_ok=True)
        self.agent = f'ArxivShelf/1.0 (personal research; {contact or "local desktop"})'
        self.opener = urllib.request.build_opener(SafeRedirect())
        self.next_request = time.monotonic() + 3.2

    def check(self):
        if self.stop.is_set():
            raise Cancelled('Stopped. Completed PDFs are kept.')

    def wait(self, seconds):
        if self.stop.wait(max(0, seconds)):
            self.check()

    def transfer(self, url, target=None):
        for attempt in range(5):
            self.check()
            self.wait(self.next_request - time.monotonic())
            try:
                request = urllib.request.Request(url, headers={'User-Agent': self.agent, 'Accept-Encoding': 'identity'})
                with self.opener.open(request, timeout=30) as response:
                    expected = response.headers.get('Content-Length')
                    maximum = 512 * 1024 * 1024 if target else 16 * 1024 * 1024
                    total = 0
                    data = bytearray()
                    stream = target.open('wb') if target else None
                    try:
                        while True:
                            self.check()
                            chunk = response.read(65536)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > maximum:
                                raise ValueError('Response exceeds the download size limit.')
                            if stream:
                                stream.write(chunk)
                            else:
                                data.extend(chunk)
                        if expected and total != int(expected):
                            raise OSError('Incomplete HTTP response.')
                        if stream:
                            stream.flush()
                            os.fsync(stream.fileno())
                    finally:
                        if stream:
                            stream.close()
                return bytes(data)
            except urllib.error.HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 4:
                    raise
                value = error.headers.get('Retry-After', '')
                error.close()
                delay = 5 * 2 ** attempt
                if value:
                    try:
                        delay = max(delay, float(value))
                    except ValueError:
                        try:
                            delay = max(delay, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError):
                            pass
                self.wait(delay)
            except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
                if attempt == 4:
                    raise
                self.wait(5 * 2 ** attempt)
            finally:
                self.next_request = time.monotonic() + 3.2
        raise RuntimeError('Request failed.')

    def page(self, query, start, end, offset):
        query = f'{query} AND submittedDate:[{start:%Y%m%d%H%M} TO {end:%Y%m%d%H%M}]'
        url = 'https://export.arxiv.org/api/query?' + urllib.parse.urlencode({
            'search_query': query, 'start': offset, 'max_results': 100,
            'sortBy': 'submittedDate', 'sortOrder': 'descending'})
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + '.xml')
        cached = path.exists()
        data = path.read_bytes() if cached else self.transfer(url)
        total, papers = parse_feed(data)
        if not cached:
            path.write_bytes(data)
        return total, papers

    def download(self, paper, target):
        temporary = target.with_suffix('.part')
        try:
            self.transfer('https://export.arxiv.org/pdf/' + paper.versioned_id, temporary)
            if not valid_pdf(temporary):
                raise ValueError(f'{paper.identifier}: response was not a complete PDF.')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def parse_feed(data):
    root = ET.fromstring(data)
    ns = {'a': 'http://www.w3.org/2005/Atom', 'o': 'http://a9.com/-/spec/opensearch/1.1/'}
    papers = []
    for entry in root.findall('a:entry', ns):
        identifier = entry.findtext('a:id', '', ns)
        if '/abs/' not in identifier:
            raise ValueError('arXiv API: ' + entry.findtext('a:summary', 'Invalid response', ns))
        papers.append(Paper.create(identifier.split('/abs/', 1)[1], entry.findtext('a:title', '', ns), entry.findtext('a:published', '', ns), [item.attrib['term'] for item in entry.findall('a:category', ns)]))
    count = root.findtext('o:totalResults', None, ns)
    if count is None:
        raise ValueError('arXiv response has no result count.')
    return int(count), papers
