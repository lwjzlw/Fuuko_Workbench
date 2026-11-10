"""风子工作台共用小库（Python 端）：本机网页服务 + 统一约定。

每个接入的工作台用它就能得到同样的行为：
- 只监听 127.0.0.1；端口可固定也可随机（port=0）。
- 校验 Host；POST 需同源 Origin 和本次运行的随机令牌（X-WB-Token）。
- GET /api/health 无需令牌，返回 {app, name, version, pid}，供风子工作台检查运行状态。
- 启动后把 {app, pid, url, started} 写进 runtime 文件，退出时删除。
- CSP 只允许自身和风子工作台（http://127.0.0.1:17800）嵌入。
- /_shared/ 下提供共用样式 fuuko.css 和前端小助手 wbkit.js。
- 简单的后台任务队列（长时间的转换、提取），前端轮询 /api/jobs/<id>。

只依赖标准库。
"""
from __future__ import annotations

import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

SHARED_DIR = Path(__file__).resolve().parent
SHARED_ASSETS = SHARED_DIR / "assets"
SHARED_PUBLIC = {SHARED_DIR / "fuuko.css", SHARED_DIR / "wbkit.js"}
HUB_PORT = 17800
HUB_ORIGIN = f"http://127.0.0.1:{HUB_PORT}"
TOKEN_PLACEHOLDER = "{{WB_TOKEN}}"

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("audio/flac", ".flac")
mimetypes.add_type("audio/ogg", ".ogg")
mimetypes.add_type("audio/ogg", ".opus")


def write_json_atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


# ---------------------------------------------------------------- 后台任务
class Job:
    def __init__(self, title: str):
        self.id = uuid.uuid4().hex[:12]
        self.title = title
        self.state = "running"   # running / done / failed
        self.log: list[str] = []
        self.result = None
        self.started = time.time()
        self.finished: float | None = None
        self._lock = threading.Lock()

    def write(self, line: str) -> None:
        with self._lock:
            self.log.extend(str(line).splitlines() or [""])
            if len(self.log) > 2000:
                del self.log[:-2000]

    def snapshot(self) -> dict:
        with self._lock:
            return {"id": self.id, "title": self.title, "state": self.state, "log": list(self.log),
                    "result": self.result, "started": self.started, "finished": self.finished}


class Jobs:
    def __init__(self, keep: int = 50):
        self._jobs: dict[str, Job] = {}
        self._keep = keep
        self._lock = threading.Lock()

    def submit(self, title: str, fn: Callable[[Job], object]) -> Job:
        job = Job(title)
        with self._lock:
            self._jobs[job.id] = job
            for old in list(self._jobs)[:-self._keep]:
                if self._jobs[old].state != "running":
                    del self._jobs[old]

        def run():
            try:
                job.result = fn(job)
                job.state = "done"
            except Exception as exc:  # 任务失败写进日志，不影响服务
                job.write(f"失败：{exc}")
                job.write(traceback.format_exc(limit=3))
                job.state = "failed"
            finally:
                job.finished = time.time()

        threading.Thread(target=run, name=f"job-{job.id}", daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[dict]:
        return [{k: v for k, v in j.snapshot().items() if k != "log"} for j in reversed(self._jobs.values())]


def run_logged(job: Job, command: list[str], cwd: str | None = None, timeout: float | None = None,
               encoding: str = "utf-8") -> int:
    """前台运行子进程，把输出逐行写进任务日志；返回退出码。"""
    job.write("$ " + subprocess.list2cmdline(command))
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + timeout if timeout else None
    assert process.stdout is not None
    for raw in process.stdout:
        job.write(raw.decode(encoding, "replace").rstrip())
        if deadline and time.monotonic() > deadline:
            process.kill()
            job.write("超时，已停止。")
            break
    return process.wait()


# ---------------------------------------------------------------- 文件选择
_PICK_SCRIPT = r"""
import json, sys, tkinter as tk
from tkinter import filedialog
kind, multiple, title, types = sys.argv[1], sys.argv[2] == "1", sys.argv[3], json.loads(sys.argv[4])
root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
if kind == "folder":
    value = filedialog.askdirectory(title=title, mustexist=True)
    result = [value] if value else []
elif kind == "save":
    value = filedialog.asksaveasfilename(title=title, filetypes=types or [("所有文件", "*.*")])
    result = [value] if value else []
elif multiple:
    result = list(filedialog.askopenfilenames(title=title, filetypes=types or [("所有文件", "*.*")]))
else:
    value = filedialog.askopenfilename(title=title, filetypes=types or [("所有文件", "*.*")])
    result = [value] if value else []
root.destroy()
sys.stdout.write(json.dumps([str(p).replace("/", "\\") for p in result], ensure_ascii=False))
"""


def pick_paths(kind: str = "file", multiple: bool = False, title: str = "选择", filetypes=None) -> list[str]:
    """弹出系统文件对话框（单独的 Python 子进程，避免和服务线程共用 Tk）。"""
    if kind not in {"file", "folder", "save"}:
        raise ValueError("kind 只能是 file / folder / save")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.is_file() else sys.executable)
    done = subprocess.run([exe, "-c", _PICK_SCRIPT, kind, "1" if multiple else "0", title,
                           json.dumps(filetypes or [], ensure_ascii=False)],
                          capture_output=True, timeout=600)
    text = done.stdout.decode("utf-8", "replace").strip()
    return json.loads(text) if text else []


# ---------------------------------------------------------------- 网页服务
class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class WorkbenchApp:
    """一个本机工作台的网页服务。

    app = WorkbenchApp("audio", "音频工作台", static_dir, runtime_file)
    @app.get("/api/x")       -> fn(query: dict) -> JSON
    @app.post("/api/y")      -> fn(body: dict) -> JSON
    @app.raw_get("/api/z")   -> fn(handler, query) 自己写响应（流式文件等）
    app.serve()
    """

    def __init__(self, app_id: str, name: str, static_dir: Path, runtime_file: Path,
                 version: str = "0.1.0", port: int = 0, embed_origins: tuple[str, ...] = (HUB_ORIGIN,),
                 frame_src: str = "'none'"):
        self.app_id = app_id
        self.name = name
        self.version = version
        self.static_dir = Path(static_dir).resolve()
        self.runtime_file = Path(runtime_file)
        self.port = port
        self.embed_origins = embed_origins
        self.frame_src = frame_src
        self.token = secrets.token_urlsafe(24)
        # 关闭密钥只写进 runtime 文件，供风子工作台从服务端请求正常退出；页面拿不到它
        self.control = secrets.token_urlsafe(24)
        self._shutdown = threading.Event()
        self.jobs = Jobs()
        self._get: dict[str, Callable] = {}
        self._post: dict[str, Callable] = {}
        self._raw_get: dict[str, Callable] = {}
        self.server: ThreadingHTTPServer | None = None
        self.last_seen = time.monotonic()
        self.post("/api/pick")(self._pick)
        self.get("/api/jobs")(lambda q: {"jobs": self.jobs.list()})

    # 路由注册
    def get(self, path: str):
        return lambda fn: self._get.__setitem__(path, fn) or fn

    def post(self, path: str):
        return lambda fn: self._post.__setitem__(path, fn) or fn

    def raw_get(self, path: str):
        return lambda fn: self._raw_get.__setitem__(path, fn) or fn

    def _pick(self, body: dict):
        return {"paths": pick_paths(body.get("kind", "file"), bool(body.get("multiple")),
                                    str(body.get("title") or "选择"), body.get("filetypes"))}

    @property
    def csp(self) -> str:
        ancestors = " ".join(("'self'", *self.embed_origins))
        return ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
                f"frame-src {self.frame_src}; "
                f"object-src 'none'; base-uri 'none'; frame-ancestors {ancestors}")

    def make_server(self) -> ThreadingHTTPServer:
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "wbkit"

            def log_message(self, fmt, *args):  # 安静，只记错误
                pass

            # ---- 校验
            def _host_ok(self) -> bool:
                port = self.server.server_port
                return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

            def _origin_ok(self) -> bool:
                origin = self.headers.get("Origin")
                port = self.server.server_port
                return origin is None or origin in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

            def _token_ok(self, query: dict | None = None) -> bool:
                given = self.headers.get("X-WB-Token") or ((query or {}).get("token") or [""])[0]
                return secrets.compare_digest(str(given), app.token)

            # ---- 响应
            def send_bytes(self, body: bytes, status: int = 200, mime: str = "application/json; charset=utf-8",
                           extra: dict | None = None):
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", app.csp)
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def send_json(self, value, status: int = 200):
                self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), status)

            def send_error_json(self, status: int, message: str):
                self.send_json({"ok": False, "error": message}, status)

            def send_file(self, path: Path, allow_range: bool = False):
                size = path.stat().st_size
                mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                start, end, status = 0, size - 1, 200
                header = self.headers.get("Range", "")
                if allow_range and header.startswith("bytes="):
                    first, _, last = header[6:].split(",")[0].partition("-")
                    try:
                        if first:
                            start = int(first)
                            end = min(int(last), size - 1) if last else size - 1
                        else:
                            start = max(0, size - int(last))
                        status = 206
                    except ValueError:
                        start, end, status = 0, size - 1, 200
                    if start > end or start >= size:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Accept-Ranges", "bytes" if allow_range else "none")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", app.csp)
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                if self.command == "HEAD":
                    return
                with path.open("rb") as handle:
                    handle.seek(start)
                    remaining = end - start + 1
                    while remaining > 0:
                        chunk = handle.read(min(1 << 16, remaining))
                        if not chunk:
                            break
                        try:
                            self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError):
                            return
                        remaining -= len(chunk)

            def _static(self, url_path: str):
                if url_path.startswith("/_shared/"):
                    base, rel = SHARED_DIR, url_path[len("/_shared/"):]
                    # 只公开样式、前端小助手和 assets\ 下的图片，不公开窗口程序和 Python 源码
                    target = (base / rel).resolve()
                    if not (target in SHARED_PUBLIC or target.is_relative_to(SHARED_ASSETS)):
                        return self.send_error_json(404, "not found")
                else:
                    base, rel = app.static_dir, url_path.lstrip("/") or "index.html"
                target = (base / rel).resolve()
                if not target.is_relative_to(base) or not target.is_file():
                    return self.send_error_json(404, "not found")
                if target.suffix == ".html":
                    body = target.read_text(encoding="utf-8").replace(TOKEN_PLACEHOLDER, app.token)
                    return self.send_bytes(body.encode("utf-8"), mime="text/html; charset=utf-8")
                return self.send_file(target)

            def do_HEAD(self):
                self.do_GET()

            def do_GET(self):
                app.touch()
                if not self._host_ok():
                    return self.send_error_json(403, "bad host")
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                if parsed.path == "/api/health":
                    return self.send_json({"ok": True, "app": app.app_id, "name": app.name,
                                           "version": app.version, "pid": os.getpid()})
                if parsed.path.startswith("/api/"):
                    if not self._token_ok(query):
                        return self.send_error_json(403, "bad token")
                    try:
                        if parsed.path in app._raw_get:
                            return app._raw_get[parsed.path](self, query)
                        if parsed.path.startswith("/api/jobs/"):
                            job = app.jobs.get(parsed.path.rsplit("/", 1)[-1])
                            return self.send_json(job.snapshot()) if job else self.send_error_json(404, "no job")
                        if parsed.path in app._get:
                            return self.send_json(app._get[parsed.path]({k: v[0] for k, v in query.items()}))
                    except HttpError as exc:
                        return self.send_error_json(exc.status, str(exc))
                    except (ValueError, FileNotFoundError) as exc:
                        return self.send_error_json(400, str(exc))
                    return self.send_error_json(404, "not found")
                return self._static(parsed.path)

            def _shutdown_request(self):
                """POST /api/shutdown：凭 runtime 文件里的关闭密钥请求正常退出；有任务在跑时默认拒绝。"""
                given = self.headers.get("X-WB-Control") or ""
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    # 先读完（很小的）请求体再回复，避免拒绝时连接被重置
                    raw = self.rfile.read(length) if 0 < length <= 4096 else b""
                except (ValueError, OSError):
                    raw = b""
                if not (self._host_ok() and self.headers.get("Origin") is None
                        and secrets.compare_digest(given, app.control)):
                    return self.send_error_json(403, "forbidden")
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = {}
                running = [j["title"] for j in app.jobs.list() if j["state"] == "running"]
                if running and not (isinstance(body, dict) and body.get("force") is True):
                    return self.send_json({"ok": False, "busy": running,
                                           "error": f"还有 {len(running)} 个任务在运行：{'、'.join(running[:3])}"}, 409)
                self.send_json({"ok": True, "app": app.app_id, "pid": os.getpid()})
                app.request_shutdown()

            def do_POST(self):
                parsed = urlparse(self.path)
                if parsed.path == "/api/shutdown":
                    return self._shutdown_request()
                if not (self._host_ok() and self._origin_ok() and self._token_ok()):
                    return self.send_error_json(403, "forbidden")
                handler = app._post.get(parsed.path)
                if not handler:
                    return self.send_error_json(404, "not found")
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > 4 << 20:
                        raise HttpError(413, "请求过大")
                    body = json.loads(self.rfile.read(length) or b"{}") if length else {}
                    if not isinstance(body, dict):
                        raise ValueError("请求体须为 JSON 对象")
                    return self.send_json(handler(body))
                except HttpError as exc:
                    return self.send_error_json(exc.status, str(exc))
                except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
                    return self.send_error_json(400, str(exc))
                except Exception as exc:
                    traceback.print_exc()
                    return self.send_error_json(500, f"内部错误：{exc}")

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = False

        return Server(("127.0.0.1", self.port), Handler)

    def url(self) -> str:
        assert self.server is not None
        return f"http://127.0.0.1:{self.server.server_port}/"

    def start(self) -> str:
        """绑定端口、写 runtime 文件，返回地址（不阻塞，用于测试）。"""
        self.server = self.make_server()
        write_json_atomic(self.runtime_file, {"app": self.app_id, "pid": os.getpid(), "url": self.url(),
                                              "started": datetime.now().isoformat(timespec="seconds"),
                                              "control": self.control})
        threading.Thread(target=self.server.serve_forever, name="wbkit-http", daemon=True).start()
        return self.url()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        try:
            current = json.loads(self.runtime_file.read_text(encoding="utf-8"))
            if current.get("pid") == os.getpid():
                self.runtime_file.unlink()
        except (OSError, ValueError):
            pass

    def serve(self, idle_minutes: float = 0) -> None:
        """阻塞运行，直到 Ctrl+C；idle_minutes>0 时，无请求且无任务超过该时长后自动退出。"""
        url = self.start()
        print(f"{self.name} 已启动：{url}", flush=True)
        try:
            self.wait(idle_minutes)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def touch(self) -> None:
        self.last_seen = time.monotonic()

    def request_shutdown(self) -> None:
        """让 wait()/serve() 返回，由调用方照常清理（删 runtime 文件、关子服务等）。"""
        self._shutdown.set()

    def wait(self, idle_minutes: float = 0) -> None:
        self.touch()
        while True:
            if self._shutdown.wait(5):
                return
            busy = any(j["state"] == "running" for j in self.jobs.list())
            if busy:
                self.touch()
            elif idle_minutes and time.monotonic() - self.last_seen > idle_minutes * 60:
                return


def read_runtime(path: Path) -> dict | None:
    """读 runtime 文件；进程已不在时返回 None。"""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    pid = value.get("pid")
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF or not pid_alive(pid):
        return None
    return value


def pid_alive(pid: int) -> bool:
    if type(pid) is not int or not 0 < pid <= 0xFFFFFFFF:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


HOST_EXE = SHARED_DIR / "host" / "bin" / "FuukoHost.exe"
DEFAULT_ICON = SHARED_DIR / "assets" / "fuuko.ico"


def host_command(url: str, profile_dir: Path, *, app_id: str = "", title: str = "", icon: str | Path | None = None,
                 width: int | None = None, height: int | None = None) -> list[str]:
    """共用窗口程序 FuukoHost.exe 的命令行（WebView2 窗口，不经过 Edge 浏览器）。"""
    command = [str(HOST_EXE), "--url", url, "--profile", str(Path(profile_dir) / "host")]
    if app_id:
        command += ["--id", app_id]
    if title:
        command += ["--title", title]
    icon_path = Path(icon) if icon else DEFAULT_ICON
    if icon_path.is_file():
        command += ["--icon", str(icon_path)]
    if width:
        command += ["--width", str(int(width))]
    if height:
        command += ["--height", str(int(height))]
    return command


def open_app_window(url: str, profile_dir: Path, *, app_id: str = "", title: str = "",
                    icon: str | Path | None = None, width: int | None = None, height: int | None = None) -> None:
    """用共用窗口程序打开独立窗口；窗口程序缺失时退回系统默认浏览器。"""
    if not HOST_EXE.is_file():
        os.startfile(url)
        return
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    subprocess.Popen(host_command(url, profile_dir, app_id=app_id, title=title, icon=icon, width=width, height=height),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
