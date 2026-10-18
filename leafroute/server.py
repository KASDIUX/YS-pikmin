import asyncio
import hmac
import json
import mimetypes
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .engine import Engine
from .geo import write_gpx
from .store import Store
from . import __version__


class LocalHTTPServer(ThreadingHTTPServer):
    # SO_REUSEADDR on Windows permits another listener on the same port.
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class App:
    def __init__(self, root, data_dir, port=8765):
        self.root = Path(root)
        self.token = secrets.token_urlsafe(32)
        self.http = LocalHTTPServer(('127.0.0.1', port), self.handler())
        self.http.daemon_threads = True
        self.port = self.http.server_address[1]
        self.origin = f'http://127.0.0.1:{self.port}'
        self.loop = asyncio.new_event_loop()
        self.engine = Engine(Store(Path(data_dir)/'library.json'))
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.ticker = asyncio.run_coroutine_threadsafe(self.engine.ticker(), self.loop)

    def call(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        # All device calls are bounded inside the engine, before this outer timeout.
        try:
            return future.result(timeout=35)
        except TimeoutError:
            future.cancel()
            raise ValueError('操作逾時，請檢查連線狀態後再試。') from None

    async def read(self, path):
        if path == '/api/state':
            return self.engine.snapshot()
        if path == '/api/library':
            return self.engine.store.data
        if path == '/api/route':
            return {'points': self.engine.route.points if self.engine.route else [], 'meta': self.engine.route_meta}
        if path == '/api/export':
            if not self.engine.route:
                raise ValueError('尚未建立路線。')
            return write_gpx(self.engine.route.points)
        raise KeyError(path)

    def handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def valid_origin(self):
                return (self.headers.get('Host') == f'127.0.0.1:{app.port}'
                        and self.headers.get('Origin', app.origin) == app.origin
                        and self.headers.get('Sec-Fetch-Site') not in ('cross-site',))

            def respond(self, status, body, content_type='application/json; charset=utf-8', download=False):
                if not isinstance(body, bytes):
                    body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store' if content_type.startswith(('application/json', 'text/html')) else 'private, max-age=3600')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-LeafRoute-Version', __version__)
                self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://tile.openstreetmap.org; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                if download:
                    self.send_header('Content-Disposition', 'attachment; filename="YS-pikmin.gpx"')
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                if not self.valid_origin():
                    return self.respond(403, {'error': '僅接受本機同來源請求。'})
                path = urlsplit(self.path).path
                try:
                    if path.startswith('/api/'):
                        result = app.call(app.read(path))
                        return self.respond(200, result, 'application/gpx+xml; charset=utf-8' if path == '/api/export' else 'application/json; charset=utf-8', download=path == '/api/export')
                    if path == '/':
                        html = (app.root/'web/index.html').read_text(encoding='utf-8').replace('__TOKEN__', app.token)
                        return self.respond(200, html.encode('utf-8'), 'text/html; charset=utf-8')
                    allowed = {'/app.js', '/style.css', '/icon.svg', '/vendor/leaflet.js', '/vendor/leaflet.css'}
                    if path not in allowed:
                        return self.respond(404, {'error': '找不到檔案。'})
                    file = app.root/'web'/path.lstrip('/')
                    mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
                    if file.suffix == '.js':
                        mime = 'application/javascript'
                    return self.respond(200, file.read_bytes(), mime)
                except KeyError:
                    self.respond(404, {'error': '找不到操作。'})
                except ValueError as exc:
                    self.respond(400, {'error': str(exc)})

            def do_POST(self):
                if not self.valid_origin() or not hmac.compare_digest(self.headers.get('X-LeafRoute-Token', ''), app.token):
                    return self.respond(403, {'error': '驗證失敗，請重新整理頁面。'})
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 2_100_000:
                        return self.respond(413, {'error': '請求大小超出限制。'})
                    if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                        return self.respond(415, {'error': '請使用 JSON。'})
                    self.connection.settimeout(10)
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError('請求格式不正確。')
                    path = urlsplit(self.path).path
                    if not path.startswith('/api/'):
                        return self.respond(404, {'error': '找不到操作。'})
                    result = app.call(app.engine.command(path.removeprefix('/api/'), data))
                    self.respond(200, result)
                except (ValueError, TypeError, OverflowError) as exc:
                    self.respond(400, {'error': str(exc)})
                except Exception:
                    self.respond(500, {'error': '操作失敗。請檢查資料夾是否可寫入，以及連線是否正常。'})

        return Handler

    def close(self):
        try:
            self.call(self.engine.shutdown())
        except Exception as exc:
            print(f'無法確認手機定位已還原：{exc}。請在手機確認，必要時重新啟動 iPhone。', flush=True)
        finally:
            self.ticker.cancel()
            self.http.server_close()
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=2)
            if not self.thread.is_alive():
                self.loop.close()
