from pathlib import Path
from queue import Queue, Empty
from threading import Event, Thread
import argparse
import base64
import signal
import subprocess
import sys
from .domain import Settings, state_directory
from .network import ArxivClient, Cancelled
from .service import DownloadLock, DownloadService
from .storage import Repository


def execute(settings, stop, emit):
    with DownloadLock():
        repository = Repository(settings.root)
        try:
            client = ArxivClient(stop, state_directory() / 'cache', settings.contact)
            return DownloadService(client, repository, stop, emit).run(settings)
        finally:
            repository.close()


class Desktop:
    def __init__(self, path):
        self.path = path
        self.settings = Settings.load(path)
        self.messages = Queue()
        self.stop = Event()
        self.worker = None
        self.closing = False

    def send(self, action, *values):
        line = '\t'.join([action] + [base64.b64encode(str(value).encode()).decode() for value in values])
        self.child.stdin.write(line + '\n')
        self.child.stdin.flush()

    def work(self, settings):
        try:
            execute(settings, self.stop, lambda text: self.messages.put(('log', text)))
        except Cancelled as error:
            self.messages.put(('log', str(error)))
        except Exception as error:
            self.messages.put(('error', str(error)))
        finally:
            self.messages.put(('done', ''))

    def run(self):
        script = Path(__file__).resolve().parents[2] / 'ui' / 'shelf.tcl'
        self.child = subprocess.Popen(['wish', str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8', bufsize=1)
        def read():
            for line in self.child.stdout:
                self.messages.put(('command', line.rstrip('\n')))
            self.messages.put(('closed', ''))
        Thread(target=read, daemon=True).start()
        s = self.settings
        self.send('init', s.root, s.subjects, s.mode, s.limit, s.contact, 'Python', s.keywords)
        try:
            while True:
                try:
                    action, value = self.messages.get(timeout=0.2)
                except Empty:
                    continue
                if action == 'closed':
                    self.stop.set()
                    if self.worker:
                        self.worker.join()
                    break
                if action == 'done':
                    self.worker.join()
                    self.worker = None
                    self.send('busy', 0)
                    if self.closing:
                        break
                elif action == 'command':
                    fields = value.split('\t')
                    command = fields[0]
                    if command in ('stop', 'quit'):
                        self.stop.set()
                        self.closing = command == 'quit'
                        if self.closing and self.worker is None:
                            break
                        self.send('log', 'Stopping after the current network operation…')
                        continue
                    if self.worker:
                        continue
                    try:
                        values = [base64.b64decode(field).decode() for field in fields[1:]]
                        root, subjects, mode, limit, contact, keywords = values
                        settings = Settings(root, subjects, mode, int(limit), contact, keywords)
                        settings.sources()
                        if command == 'open':
                            Path(root).mkdir(parents=True, exist_ok=True)
                            subprocess.Popen(['xdg-open', root], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        elif command in ('start', 'save'):
                            settings.save(self.path)
                            self.settings = settings
                            self.send('log', 'Settings saved.')
                            if command == 'start':
                                self.stop.clear()
                                self.send('busy', 1)
                                self.worker = Thread(target=self.work, args=(settings,))
                                self.worker.start()
                    except Exception as error:
                        self.send('error', str(error))
                else:
                    self.send(action, value)
        finally:
            self.stop.set()
            if self.worker:
                self.worker.join()
            self.child.terminate()
            self.child.wait()


def main():
    parser = argparse.ArgumentParser(description='Private arXiv desktop library')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--mode', choices=['limited', 'daily', 'all'])
    parser.add_argument('--limit', type=int)
    parser.add_argument('--config', type=Path, default=state_directory() / 'settings.json')
    args = parser.parse_args()
    try:
        if not args.headless:
            Desktop(args.config).run()
            return 0
        settings = Settings.load(args.config)
        if args.mode:
            settings.mode = args.mode
        if args.limit is not None:
            settings.limit = args.limit
        settings.sources()
        stop = Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        execute(settings, stop, lambda text: print(text, flush=True))
        return 0
    except Cancelled as error:
        print(error, file=sys.stderr)
        return 130
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
