from pathlib import Path
import http.client
import ipaddress
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from .processes import PdfProcessRunner
from .domain import ContextLengthError, DocumentError, OllamaUnavailableError, ConfigurationError
from .limits import (
    MAX_PAGE_BYTES, MAX_RESPONSE_BYTES, HTTP_TIMEOUT_SECONDS, HTTP_ATTEMPTS, RETRY_STATUSES, MAX_TIMEOUT_SECONDS,
)


def loopback_url(value):
    value = value if '://' in value else 'http://' + value
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != 'http' or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('Ollama address must be an HTTP loopback host and port without credentials or a path.')
    host = parsed.hostname
    if host == 'localhost':
        host = '127.0.0.1'
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError('Ollama must use a loopback address.')
    except ValueError as error:
        raise ValueError('Ollama must use localhost or a numeric loopback address.') from error
    port = 11434 if parsed.port is None else parsed.port
    if port < 1:
        raise ValueError('Invalid Ollama port.')
    host = f'[{host}]' if ':' in host else host
    return f'http://{host}:{port}/api/'


def context_error(message):
    value = message.lower()
    return any(marker in value for marker in (
        'input length exceeds', 'context length exceeded', 'exceeds the context',
        'exceeds context', 'too many tokens', 'context window exceeded',
    ))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ConfigurationError('Ollama redirects are not allowed.')


class Ollama:
    def __init__(self, model, address=None, timeout=HTTP_TIMEOUT_SECONDS, keep_alive='10m'):
        if not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
            raise ValueError('HTTP timeout must be 1–600 seconds.')
        self.model, self.timeout, self.keep_alive = model, timeout, keep_alive
        environment_address = os.environ.get('OLLAMA_HOST')
        source = '--ollama-url' if address is not None else ('OLLAMA_HOST' if environment_address is not None else 'default address')
        selected = address if address is not None else (environment_address if environment_address is not None else '127.0.0.1:11434')
        try:
            self.base = loopback_url(selected)
        except ValueError as error:
            raise ConfigurationError(
                f'Invalid {source}: {selected!r}. This is a client address, not a server bind address. '
                'Use --ollama-url http://127.0.0.1:11434 or unset OLLAMA_HOST. '
                'Wildcard bind addresses are not valid client destinations.'
            ) from error
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, endpoint, data=None):
        for attempt in range(HTTP_ATTEMPTS):
            try:
                request = urllib.request.Request(self.base + endpoint,
                    data=json.dumps(data).encode() if data is not None else None,
                    headers={'Content-Type': 'application/json'})
                with self.client.open(request, timeout=self.timeout) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise OllamaUnavailableError('Ollama response exceeded its size limit.')
                    try:
                        value = json.loads(raw)
                        if not isinstance(value, dict):
                            raise OllamaUnavailableError('Ollama returned an unexpected JSON structure.')
                        return value
                    except ValueError as error:
                        raise OllamaUnavailableError('Ollama returned invalid JSON.') from error
            except urllib.error.HTTPError as error:
                code = error.code
                try:
                    message = error.read(4096).decode(errors='replace')
                except (OSError, http.client.HTTPException):
                    message = 'Failed to read the HTTP error body.'
                finally:
                    error.close()
                if context_error(message):
                    raise ContextLengthError(f'Ollama HTTP {code}: {message}') from error
                if code not in RETRY_STATUSES or attempt == HTTP_ATTEMPTS - 1:
                    raise OllamaUnavailableError(f'Ollama HTTP {code}: {message}') from error
            except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
                if attempt == HTTP_ATTEMPTS - 1:
                    raise OllamaUnavailableError(f'Ollama transport failed: {error}') from error
            time.sleep(2 ** attempt)
        raise RuntimeError('Ollama request failed.')

    def digest(self):
        for item in self.request('tags').get('models', []):
            if item.get('name') == self.model:
                return item['digest']
        raise ValueError(f'Model is not installed under the exact name: {self.model}')

    def embed(self, texts):
        result = self.request('embed', {'model': self.model, 'input': texts, 'truncate': False, 'keep_alive': self.keep_alive})
        values = result.get('embeddings', [])
        if not isinstance(values, list) or len(values) != len(texts) or any(not isinstance(row, list) for row in values):
            raise OllamaUnavailableError('Ollama returned an unexpected number of embeddings.')
        return values


class PopplerExtractor:
    def __init__(self, runner=None):
        self.runner = runner or PdfProcessRunner()

    def pages(self, path):
        self.runner.require('prlimit', 'util-linux')
        executable = self.runner.require('pdftotext', 'poppler-utils')
        path = Path(path).resolve(strict=True)
        with tempfile.TemporaryDirectory(prefix='rag-shelf-') as directory:
            output = Path(directory) / 'document.txt'
            self.runner.run([executable, '-enc', 'UTF-8', str(path), str(output)], path, Path(directory))
            page = 1
            buffer = bytearray()
            with output.open('rb') as stream:
                while data := stream.read(65536):
                    buffer.extend(data)
                    while (boundary := buffer.find(b'\x0c')) >= 0:
                        if boundary > MAX_PAGE_BYTES:
                            raise DocumentError('Extracted page exceeds 4 MiB.')
                        current = buffer[:boundary]
                        del buffer[:boundary + 1]
                        yield page, current.decode('utf-8', errors='replace')
                        page += 1
                    if len(buffer) > MAX_PAGE_BYTES:
                        raise DocumentError('Extracted page exceeds 4 MiB.')
                if buffer.strip():
                    yield page, buffer.decode('utf-8', errors='replace')
