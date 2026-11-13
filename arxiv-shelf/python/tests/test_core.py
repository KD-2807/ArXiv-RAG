from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase
from unittest.mock import patch
import io
import urllib.error
from arxiv_shelf.domain import Paper, Settings, Source
from arxiv_shelf.network import ArxivClient, Cancelled, parse_feed
from arxiv_shelf.service import DownloadLock, DownloadService, windows
from arxiv_shelf.storage import Repository, valid_pdf

PDF = b'%PDF-1.4\nfixture\n%%EOF\n'


def paper(number=1):
    return Paper.create(f'2611.{number:05d}v1', 'Test', '2026-11-01T00:00:00Z', ['cs.SD'])


class FakeCatalog:
    def __init__(self, papers, fail=False):
        self.items = papers
        self.calls = 0
        self.downloads = 0
        self.fail = fail

    def page(self, category, start, end, offset):
        self.calls += 1
        return len(self.items), self.items[offset:offset + 1]

    def download(self, item, target):
        if self.fail:
            raise OSError('offline')
        self.downloads += 1
        target.write_bytes(PDF)


class CoreTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = Repository(self.root)

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def settings(self, mode='daily', limit=100, subjects='cs.SD=Sound', keywords=''):
        return Settings(str(self.root), subjects, mode, limit, keywords=keywords)

    def test_feed_namespaces_and_old_ids(self):
        total, entries = parse_feed((Path(__file__).parents[2] / 'tests/feed.xml').read_bytes())
        self.assertEqual(total, 2)
        self.assertEqual(entries[0].title, 'Sound & Robotics')
        self.assertEqual(entries[0].categories, ('cs.SD', 'cs.RO'))
        self.assertEqual(entries[1].filename, 'hep-th_9901001.pdf')

    def test_api_error(self):
        with self.assertRaisesRegex(ValueError, 'bad query'):
            parse_feed(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>error</id><summary>bad query</summary></entry></feed>')

    def test_validation(self):
        for subjects in ['cs.*=All', 'cs.SD=../escape', 'cs.SD=Sound\ncs.SD=Other', '']:
            with self.subTest(subjects=subjects), self.assertRaises(ValueError):
                self.settings(subjects=subjects).sources()
        with self.assertRaises(ValueError):
            Paper.create('../../tmp/x', '', '', [])

    def test_keyword_sources(self):
        url = 'https://arxiv.org/search/?query=connectome&searchtype=all&source=header'
        cases = {
            'connectome=Connectome': Source('kw:all:connectome', 'connectome', 'all:connectome', 'Connectome', False),
            'brain connectome=Brain': Source('kw:(all:brain AND all:connectome)', 'brain connectome', '(all:brain AND all:connectome)', 'Brain', False),
            '"graph theory"=Graphs': Source('kw:all:"graph theory"', '"graph theory"', 'all:"graph theory"', 'Graphs', False),
            url: Source('kw:all:connectome', 'connectome', 'all:connectome', 'connectome', False),
            url + ' My Papers': Source('kw:all:connectome', 'connectome', 'all:connectome', 'My Papers', False),
            url.replace('searchtype=all', 'searchtype=title'): Source('kw:ti:connectome', 'connectome', 'ti:connectome', 'connectome', False),
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(self.settings(subjects='', keywords=line).sources(), [expected])
        mixed = self.settings(keywords='connectome=Connectome').sources()
        self.assertEqual([source.key for source in mixed], ['cs.SD', 'kw:all:connectome'])
        self.assertTrue(mixed[0].monthly)
        self.assertFalse(mixed[1].monthly)

    def test_keyword_validation(self):
        for keywords in ['connectome', '=Folder', 'a;b=Folder', 'x=../escape', 'https://evil.example/search/?query=a',
                         'https://arxiv.org/search/?query=a&searchtype=bogus', 'a=F\na=G']:
            with self.subTest(keywords=keywords), self.assertRaises(ValueError):
                self.settings(subjects='', keywords=keywords).sources()

    def test_keyword_run_uses_one_range_and_own_checkpoint(self):
        fake = FakeCatalog([paper()])
        queries = []
        original = fake.page
        fake.page = lambda query, start, end, offset: (queries.append((query, start, end)), original(query, start, end, offset))[1]
        DownloadService(fake, self.repo, Event(), lambda _: None).run(self.settings(subjects='', keywords='connectome=Connectome'))
        self.assertEqual({query for query, _, _ in queries}, {'all:connectome'})
        self.assertEqual(len(queries), 1)
        self.assertTrue((self.root / 'Connectome/2611.00001.pdf').exists())
        self.assertIsNotNone(self.repo.checkpoint('kw:all:connectome'))
        self.assertIsNone(self.repo.checkpoint('cs.SD'))

    def test_query_is_sent_to_arxiv(self):
        client = ArxivClient(Event(), self.root / 'cache')
        urls = []
        client.transfer = lambda url: (urls.append(url), (self.root / 'tests.xml').read_bytes())[1]
        (self.root / 'tests.xml').write_bytes((Path(__file__).parents[2] / 'tests/feed.xml').read_bytes())
        client.page('(all:brain AND all:connectome)', datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc), 0)
        self.assertIn('search_query=%28all%3Abrain+AND+all%3Aconnectome%29+AND+submittedDate%3A', urls[0])

    def test_skip_restart_and_cross_list(self):
        fake = FakeCatalog([paper()])
        service = DownloadService(fake, self.repo, Event(), lambda _: None)
        service.run(self.settings(subjects='cs.SD=Sound\ncs.RO=Robotics'))
        self.assertEqual(fake.downloads, 1)
        self.assertTrue((self.root / 'Sound/2611.00001.pdf').samefile(self.root / 'Robotics/2611.00001.pdf'))
        service.run(self.settings())
        self.assertEqual(fake.downloads, 1)

    def test_limit_counts_new_not_skips(self):
        item = paper()
        self.repo.path(item).write_bytes(PDF)
        self.repo.record(item)
        fake = FakeCatalog([item, paper(2), paper(3)])
        DownloadService(fake, self.repo, Event(), lambda _: None).run(self.settings('limited', 1))
        self.assertEqual(fake.downloads, 1)
        self.assertTrue(self.repo.contains(paper(2)))
        self.assertFalse(self.repo.contains(paper(3)))

    def test_corruption_and_missing_file(self):
        item = paper()
        path = self.repo.path(item)
        path.write_bytes(PDF)
        self.repo.record(item)
        path.write_bytes(PDF.replace(b'fixture', b'changed'))
        self.assertFalse(self.repo.contains(item))
        path.unlink()
        self.assertFalse(self.repo.contains(item))

    def test_recover_orphan_after_rename(self):
        item = paper()
        self.repo.path(item).write_bytes(PDF)
        self.assertTrue(self.repo.contains(item))
        self.assertIsNotNone(self.repo.db.execute('SELECT id FROM papers').fetchone())

    def test_failure_does_not_advance_checkpoint(self):
        with self.assertRaises(OSError):
            DownloadService(FakeCatalog([paper()], True), self.repo, Event(), lambda _: None).run(self.settings())
        self.assertIsNone(self.repo.checkpoint('cs.SD'))

    def test_cancel_does_not_download(self):
        stop = Event()
        stop.set()
        fake = FakeCatalog([paper()])
        with self.assertRaises(Cancelled):
            DownloadService(fake, self.repo, stop, lambda _: None).run(self.settings())
        self.assertEqual(fake.downloads, 0)

    def test_subject_symlink_rejected(self):
        self.repo.path(paper()).write_bytes(PDF)
        with TemporaryDirectory() as other:
            (self.root / 'Sound').symlink_to(other)
            with self.assertRaises(ValueError):
                self.repo.link(paper(), 'Sound')

    def test_empty_page_is_not_success(self):
        fake = FakeCatalog([])
        fake.page = lambda *args: (1, [])
        with self.assertRaisesRegex(RuntimeError, 'incomplete page'):
            DownloadService(fake, self.repo, Event(), lambda _: None).run(self.settings())

    def test_split_has_no_gap(self):
        start = datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)
        end = datetime(2026, 11, 1, 0, 1, tzinfo=timezone.utc)
        result = list(windows(start, end))
        self.assertEqual(len(result), 2)
        self.assertEqual((result[0][0] - result[1][1]).total_seconds(), 60)

    def test_adaptive_partition(self):
        fake = FakeCatalog([])
        calls = []
        def page(category, start, end, offset):
            calls.append((start, end))
            return (1001, []) if len(calls) == 1 else (0, [])
        fake.page = page
        service = DownloadService(fake, self.repo, Event(), lambda _: None)
        start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        end = datetime(2026, 11, 2, tzinfo=timezone.utc)
        self.assertEqual(list(service.papers('cs.SD', start, end)), [])
        self.assertEqual(len(calls), 3)
        self.assertEqual((calls[1][0] - calls[2][1]).total_seconds(), 60)

    def test_partial_file_not_committed(self):
        client = ArxivClient(Event(), self.root / 'cache')
        client.transfer = lambda url, path: path.write_bytes(b'<html>failure</html>')
        with self.assertRaises(ValueError):
            client.download(paper(), self.repo.path(paper()))
        self.assertFalse(self.repo.path(paper()).exists())
        self.assertFalse(self.repo.path(paper()).with_suffix('.part').exists())

    def test_lock_excludes_other_jobs(self):
        with patch.dict('os.environ', {'XDG_CACHE_HOME': str(self.root)}):
            with DownloadLock():
                with self.assertRaises(RuntimeError):
                    with DownloadLock():
                        pass

    def test_retry_after_honored(self):
        client = ArxivClient(Event(), self.root / 'cache')
        waits = []
        client.wait = waits.append
        class Response(io.BytesIO):
            headers = {'Content-Length': '2'}
        responses = [urllib.error.HTTPError('https://export.arxiv.org', 429, 'slow', {'Retry-After': '17'}, None), Response(b'ok')]
        def open_request(*args, **kwargs):
            value = responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        client.opener.open = open_request
        self.assertEqual(client.transfer('https://export.arxiv.org'), b'ok')
        self.assertIn(17.0, waits)
