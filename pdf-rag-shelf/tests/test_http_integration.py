from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
import json
import shutil
from rag_shelf.adapters import Ollama, PopplerExtractor
from rag_shelf.domain import Profile
from rag_shelf.storage import Repository
from rag_shelf.service import IndexService, SearchService


class LoopbackHttpTests(TestCase):
    def test_real_http_pdf_index_and_search_contract(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, payload):
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path != '/api/tags':
                    self.send_error(404)
                    return
                self.respond({'models': [{'name': 'fixture', 'digest': 'fixture-digest'}]})

            def do_POST(self):
                if self.path != '/api/embed':
                    self.send_error(404)
                    return
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(payload)
                self.respond({'embeddings': [[1.0, float(len(text) % 7 + 1)] for text in payload['input']]})
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = Ollama('fixture', f'http://127.0.0.1:{server.server_port}', timeout=5)
            profile = Profile('fixture', client.digest())
            with TemporaryDirectory() as temporary:
                root = Path(temporary)
                shutil.copyfile(Path(__file__).with_name('fixture.pdf'), root / 'fixture.pdf')
                repo = Repository(root / 'index.sqlite3')
                try:
                    service = IndexService(client, PopplerExtractor(), repo, lambda *_: None)
                    self.assertEqual(service.index(root, profile), 0)
                    hits = SearchService(client, repo).search('audio', 2)
                    self.assertTrue(hits)
                    self.assertTrue(all(hit['page'] in (1, 2) and hit['sources'] for hit in hits))
                    self.assertTrue(all(request['truncate'] is False for request in requests))
                    self.assertTrue(any(len(request['input']) > 1 for request in requests))
                finally:
                    repo.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
