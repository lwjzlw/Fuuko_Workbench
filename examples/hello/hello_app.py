"""Standalone standard-library example of Fuuko's local service protocol."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
from urllib.parse import urlparse
import webbrowser

APP_ID = 'example-workbench'
PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>示例工作台</title>
<style>body{font:18px system-ui;max-width:46rem;margin:12vh auto;padding:2rem;background:#f8f7f3;color:#25343a}
h1{color:#557c88}p{line-height:1.8}</style><h1>工作台已接入</h1>
<p>这个示例拥有独立的说明文件和本机服务，仅使用 Python 标准库。</p>
<p>可以从风子的管理页关闭服务，也可以独立运行。</p></html>'''.encode('utf-8')


def serve(data_dir: Path, *, open_browser: bool = False) -> None:
    stopped = threading.Event()
    control = secrets.token_urlsafe(24)
    runtime_file = data_dir / 'runtime.json'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status: int, value, mime: str = 'application/json; charset=utf-8'):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self' http://127.0.0.1:17800")
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def do_GET(self):
            if not self.host_ok():
                return self.reply(403, {'error': 'bad host'})
            path = urlparse(self.path).path
            if path == '/api/health':
                return self.reply(200, {'ok': True, 'app': APP_ID, 'pid': os.getpid(), 'version': '0.1.0'})
            if path == '/':
                return self.reply(200, PAGE, 'text/html; charset=utf-8')
            self.reply(404, {'error': 'not found'})

        def do_POST(self):
            if (not self.host_ok() or self.headers.get('Origin') is not None or
                    not secrets.compare_digest(self.headers.get('X-WB-Control', ''), control)):
                return self.reply(403, {'error': 'forbidden'})
            if urlparse(self.path).path != '/api/shutdown':
                return self.reply(404, {'error': 'not found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 4096:
                    return self.reply(413, {'error': 'invalid request size'})
                self.rfile.read(length)
            except ValueError:
                return self.reply(400, {'error': 'invalid request size'})
            self.reply(200, {'ok': True, 'app': APP_ID, 'pid': os.getpid()})
            stopped.set()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    url = f'http://127.0.0.1:{server.server_port}/'
    data_dir.mkdir(parents=True, exist_ok=True)
    temporary = runtime_file.with_name(f'runtime.{secrets.token_hex(6)}.tmp')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    try:
        temporary.write_text(json.dumps({'app': APP_ID, 'pid': os.getpid(), 'url': url, 'control': control}), encoding='utf-8')
        temporary.replace(runtime_file)
        thread.start()
        print(url, flush=True)
        if open_browser:
            webbrowser.open(url)
        stopped.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if thread.is_alive():
            server.shutdown()
        server.server_close()
        thread.join(timeout=2) if thread.ident else None
        temporary.unlink(missing_ok=True)
        try:
            if json.loads(runtime_file.read_text(encoding='utf-8')).get('pid') == os.getpid():
                runtime_file.unlink()
        except (OSError, ValueError):
            pass


def main() -> int:
    default_root = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / '.local' / 'share'))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=default_root / 'FuukoWorkbenches' / APP_ID)
    parser.add_argument('--serve', action='store_true', help='Start service without opening the browser')
    args = parser.parse_args()
    serve(args.data_dir, open_browser=not args.serve)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
