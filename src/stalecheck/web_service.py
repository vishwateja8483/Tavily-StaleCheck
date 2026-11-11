"""Explicit loopback HTTP authority; never a general-purpose file server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import socket
import threading

from .contracts import MAX_FILE, MAX_TOTAL, ValidationError, decode, json_bytes, require, safe_path
from .web_jobs import ServiceError

MAX_BODY = 100_000
CSP = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-src 'self' about:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


class AssessmentServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 8

    def __init__(self, manager, static_root=None, *, port=0, max_handlers=8):
        require(type(port) is int and 0 <= port <= 65535, 'invalid port')
        require(type(max_handlers) is int and 1 <= max_handlers <= 32, 'invalid handler bound')
        self.manager = manager
        self.token = secrets.token_urlsafe(32)
        self.slots = threading.BoundedSemaphore(max_handlers)
        self.static_root = Path(static_root or Path(__file__).parent / 'assessment_static').absolute()
        require(safe_path(self.static_root, 'index.html').is_file(), 'assessment assets missing; build the dedicated assessment UI first')
        self.assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/index.html': ('index.html', 'text/html; charset=utf-8')}
        assets = safe_path(self.static_root, 'assets')
        if assets.is_dir():
            for path in assets.iterdir():
                if re.fullmatch(r'[A-Za-z0-9_-]+\.(js|css)', path.name):
                    safe_path(assets, path.name)
                    self.assets['/assets/' + path.name] = ('assets/' + path.name,
                        'text/javascript; charset=utf-8' if path.suffix == '.js' else 'text/css; charset=utf-8')
        try:
            super().__init__(('127.0.0.1', port), Handler)
        except OSError:
            raise ValueError('Cannot bind the local assessment service; close the conflicting service or use --port 0.') from None
        self.origin = f'http://127.0.0.1:{self.server_port}'
        self.host = f'127.0.0.1:{self.server_port}'

    @classmethod
    def create(cls, manager_factory, static_root=None, *, port=0, max_handlers=8):
        """Acquire the listener before reserving an exclusive session directory."""
        server = cls(None, static_root, port=port, max_handlers=max_handlers)
        try:
            server.manager = manager_factory()
        except BaseException:
            server.server_close()
            raise
        return server

    @property
    def launch_url(self):
        return self.origin + '/#token=' + self.token

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(5)
        return request, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        # Absolute handler lifetime also bounds a peer dripping bytes below the
        # per-read timeout. Provider work is in the separate collector thread.
        timer = threading.Timer(10, expire)
        timer.daemon = True
        timer.start()
        try:
            super().process_request_thread(request, client_address)
        finally:
            timer.cancel()
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # Do not dump answers, headers or raw exceptions.

    def server_close(self):
        try:
            if self.manager is not None:
                self.manager.close()
        finally:
            super().server_close()


class Handler(BaseHTTPRequestHandler):
    server_version = 'StaleCheck'
    sys_version = ''

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self._respond(code, json_bytes({'error': 'request rejected'}))

    def _respond(self, code, raw, kind='application/json; charset=utf-8', filename=None):
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Connection', 'close')
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(raw)

    def _boundary(self, mutation):
        # Reject duplicate authority/framing/security headers rather than choosing one.
        for name in ('Host', 'Origin', 'Content-Length', 'Content-Type', 'X-Assessment-Token', 'Sec-Fetch-Site', 'Transfer-Encoding'):
            if len(self.headers.get_all(name, [])) > 1:
                raise ServiceError(400, 'duplicate request header')
        if self.headers.get('Host') != self.server.host:
            raise ServiceError(403, 'invalid host')
        if self.headers.get('Sec-Fetch-Site') not in (None, 'none', 'same-origin'):
            raise ServiceError(403, 'cross-site request rejected')
        if self.headers.get('Transfer-Encoding') is not None:
            raise ServiceError(400, 'transfer encoding not supported')
        if self.headers.get('Expect') is not None:
            raise ServiceError(400, 'expectation not supported')
        if not self.path.startswith('/') or any(c in self.path for c in ('?', '#', '%', '\\')):
            raise ServiceError(404, 'unknown route')
        if self.path.startswith('/api/'):
            token = self.headers.get('X-Assessment-Token', '')
            if not secrets.compare_digest(token.encode('utf-8'), self.server.token.encode()):
                raise ServiceError(401, 'launch token required')
        if mutation:
            if self.headers.get('Origin') != self.server.origin:
                raise ServiceError(403, 'same-origin mutation required')
            if self.headers.get('Content-Type') != 'application/json':
                raise ServiceError(415, 'JSON content type required')
        elif self.headers.get('Content-Length') not in (None, '0'):
            raise ServiceError(400, 'unexpected request body')

    def _body(self):
        length = self.headers.get('Content-Length', '')
        if not re.fullmatch(r'[0-9]{1,6}', length):
            raise ServiceError(400, 'bounded content length required')
        size = int(length)
        if not 0 < size <= MAX_BODY:
            raise ServiceError(413, 'request body exceeds bound')
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise ServiceError(400, 'incomplete request body')
        value = decode(raw)
        if not isinstance(value, dict):
            raise ServiceError(400, 'JSON object required')
        return value

    def _dispatch(self, mutation=False):
        try:
            self._boundary(mutation)
            path = self.path
            manager = self.server.manager
            code, filename, kind = 200, None, 'application/json; charset=utf-8'
            if mutation:
                body = self._body()
                if path == '/api/jobs':
                    if set(body) != {'idempotency_key', 'input'}:
                        raise ServiceError(400, 'invalid creation fields')
                    value, created = manager.create(body['idempotency_key'], body['input'])
                    code = 201 if created else 200
                else:
                    match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})/(approval|cancel)', path)
                    if not match:
                        raise ServiceError(404, 'unknown route')
                    identifier, action = match.groups()
                    if action == 'cancel' and not body:
                        value = manager.cancel(identifier)
                    elif action == 'approval' and set(body) == {'request', 'approved'}:
                        value = manager.approve(identifier, body['request'], body['approved'])
                    else:
                        raise ServiceError(400, 'invalid decision fields')
                raw = json_bytes(value)
            elif path == '/api/capabilities':
                raw = json_bytes(manager.capabilities())
            elif path in self.server.assets:
                relative, kind = self.server.assets[path]
                with safe_path(self.server.static_root, relative).open('rb') as stream:
                    raw = stream.read(MAX_FILE + 1)
            else:
                match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})(?:/(report|artifacts/([a-z.-]+)))?', path)
                if not match:
                    raise ServiceError(404, 'unknown route')
                identifier, action, filename = match.groups()
                if not action:
                    raw = json_bytes(manager.get(identifier).snapshot())
                else:
                    raw = manager.artifact(identifier, filename or 'report.json')
                    if filename == 'report.html':
                        kind = 'text/html; charset=utf-8'
                    elif filename == 'verified-run.zip':
                        kind = 'application/zip'
            limit = MAX_TOTAL + MAX_FILE + 65_536 if kind == 'application/zip' else MAX_FILE
            if len(raw) > limit:
                raise ServiceError(413, 'response exceeds bound')
            self._respond(code, raw, kind, filename)
        except ServiceError as exc:
            self._respond(exc.status, json_bytes({'error': str(exc)}))
        except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
            self._respond(400, json_bytes({'error': 'invalid request or artifact'}))
        except (ConnectionError, socket.timeout):
            self.close_connection = True
        except Exception:
            # Storage or unexpected failures: a definite generic answer, never internals.
            try:
                self._respond(500, json_bytes({'error': 'internal service error'}))
            except OSError:
                self.close_connection = True

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch(True)

    def do_OPTIONS(self):
        self.send_error(405)
