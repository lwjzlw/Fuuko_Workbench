"""工作台说明文件协议、状态检查和启动。登记持久化见 registration.py。"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from shared import wbkit

HERE = Path(__file__).resolve().parent
WORKBENCH_ROOT = HERE.parent
APPS_DIR = HERE / "apps"
SCHEMA_VERSION = 1
HOST_API = 1
OPEN_MODES = {"tab", "window", "launch"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


class ManifestError(ValueError):
    pass


def _string(value, field: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value or (not empty and not value.strip()):
        raise ManifestError(f"{field} 须为{'可为空的' if empty else '非空'}字符串")
    return value


def _object(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestError(f"{field} 须为对象")
    return value


def _integer(value, field: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ManifestError(f"{field} 须为 {low}–{high} 的整数")
    return value


def _command(value, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ManifestError(f"{field} 须为非空字符串数组")
    for i, part in enumerate(value):
        _string(part, field, empty=i > 0)
    return list(value)


def default_workbench_data_root() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".local" / "share"))
    return root / "FuukoWorkbenches"


def _placeholders(base: Path | None = None, runtime_paths: dict | None = None) -> dict[str, str]:
    configured = runtime_paths or {}
    python = Path(sys.executable)
    if base:
        candidates = [base / ".venv" / "Scripts" / "python.exe", base / ".venv" / "bin" / "python"]
        python = next((p for p in candidates if p.is_file()), python)
    if configured.get("python"):
        python = Path(configured["python"])
    pythonw = python.with_name("pythonw.exe")
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        powershell = str(Path(os.environ.get("SystemRoot", "C:/Windows")) /
                         "System32/WindowsPowerShell/v1.0/powershell.exe")
    return {
        "{python}": str(python),
        "{pythonw}": configured.get("pythonw") or str(pythonw if pythonw.is_file() else python),
        "{node}": configured.get("node") or shutil.which("node") or "node",
        "{powershell}": configured.get("powershell") or powershell,
    }


def _local_url(value: str) -> str:
    _string(value, "url")
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise ManifestError(f"本机地址无效：{value}") from exc
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or not port or parsed.username is not None or parsed.password is not None):
        raise ManifestError(f"只允许带端口的本机 http 地址：{value}")
    return value


def _replace(value: str, values: dict[str, str]) -> str:
    for key, replacement in values.items():
        value = value.replace(key, replacement)
    return value


def validate(raw: dict, source: Path, *, data_dir: Path | str | None = None,
             runtime_paths: dict | None = None) -> dict:
    """校验协议并解析路径；不创建目录、启动程序或读取健康检查地址。"""
    _object(raw, "说明文件")
    source = Path(source).resolve()
    base = source.parent
    schema = raw.get("schema_version", 0)
    if type(schema) is not int or schema not in {0, SCHEMA_VERSION} or ("schema_version" in raw and schema == 0):
        raise ManifestError(f"不支持的 schema_version：{schema!r}；支持 {SCHEMA_VERSION}")
    app_id = _string(raw.get("id"), "id").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", app_id):
        raise ManifestError("id 只能由英文字母、数字、- 和 _ 组成")
    version = raw.get("version", "")
    if schema:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", app_id):
            raise ManifestError("新协议 id 须以小写英文字母或数字开头，只能包含小写英文字母、数字、- 和 _")
        _string(version, "version")
        if not _VERSION.fullmatch(version):
            raise ManifestError("version 须为语义版本，例如 1.0.0")
        requires = _object(raw.get("requires"), "requires")
        host_api = requires.get("host_api")
        if type(host_api) is not int or host_api != HOST_API:
            raise ManifestError(f"不支持的 requires.host_api：{host_api!r}；支持 {HOST_API}")
    elif version:
        _string(version, "version")
    repository = _string(raw.get("repository", ""), "repository", empty=True)
    if repository:
        repo = urlparse(repository)
        if repo.scheme not in {"http", "https"} or not repo.hostname or repo.username is not None or repo.password is not None:
            raise ManifestError("repository 须为不含凭据的 http(s) 地址")
    runtime_paths = {} if runtime_paths is None else runtime_paths
    _object(runtime_paths, "runtime_paths")
    for key, value in runtime_paths.items():
        if key not in {"python", "pythonw", "node", "powershell"}:
            raise ManifestError(f"未知的运行时：{key}")
        _string(value, f"runtime_paths.{key}")
    data_path = Path(data_dir) if data_dir is not None else default_workbench_data_root() / app_id
    data_path = data_path.expanduser().resolve()
    values = _placeholders(base, runtime_paths) | {"{app_dir}": str(base), "{data_dir}": str(data_path)}

    def path(value, field):
        return str((base / _replace(_string(value, field), values)).resolve())

    def command(value, field):
        return [_replace(part, values) for part in _command(value, field)]

    app = {
        "id": app_id, "name": _string(raw.get("name"), "name").strip(),
        "category": _string(raw.get("category", "其他"), "category"),
        "description": _string(raw.get("description", ""), "description", empty=True),
        "accent": _string(raw.get("accent", ""), "accent", empty=True),
        "order": _integer(raw.get("order", 100), "order", -1000000, 1000000),
        "note": _string(raw.get("note", ""), "note", empty=True),
        "source": str(source), "schema_version": schema, "legacy": schema == 0,
        "version": version, "host_api": HOST_API if schema else None,
        "repository": repository, "data_dir": str(data_path), "available": True, "error": "",
    }
    icon = raw.get("icon", "")
    _string(icon, "icon", empty=True)
    app["icon"] = path(icon, "icon") if icon else ""
    launch = raw.get("launch")
    app["launch"] = None
    if launch is not None:
        _object(launch, "launch")
        app["launch"] = {"command": command(launch.get("command"), "launch.command"),
                         "cwd": path(launch.get("cwd", "."), "launch.cwd")}
    url = raw.get("url")
    url = {} if url is None else _object(url, "url")
    if "fixed" in url and "runtime" in url:
        raise ManifestError("url.fixed 和 url.runtime 只能填写一种")
    if "fixed" in url:
        app["url"] = {"fixed": _local_url(url["fixed"])}
    elif "runtime" in url:
        app["url"] = {"runtime": path(url["runtime"], "url.runtime")}
    elif url:
        raise ManifestError("url 须包含 fixed 或 runtime")
    else:
        app["url"] = None
    health = raw.get("health")
    health = {} if health is None else dict(_object(health, "health"))
    if not health and app["url"]:
        if "fixed" in app["url"]:
            health = {"type": "port", "port": urlparse(app["url"]["fixed"]).port}
        else:
            health = {"type": "runtime", "path": app["url"]["runtime"]}
    kind = health.get("type")
    if kind == "http":
        health["url"] = _local_url(health.get("url"))
        if "expect_app" in health:
            _string(health["expect_app"], "health.expect_app")
    elif kind == "port":
        health["port"] = _integer(health.get("port"), "health.port", 1, 65535)
    elif kind == "runtime":
        health["path"] = path(health.get("path", url.get("runtime")), "health.path")
    elif kind == "process":
        health["process"] = _process_name(health.get("process"))
    elif health:
        raise ManifestError(f"未知的 health.type：{kind!r}")
    app["health"] = health or None
    modes = raw.get("open", ["launch"])
    if not isinstance(modes, list) or not modes or not all(isinstance(m, str) and m in OPEN_MODES for m in modes):
        raise ManifestError("open 须为 tab / window / launch 组成的非空数组")
    app["open"] = list(dict.fromkeys(modes))
    if ("tab" in modes or "window" in modes) and not app["url"]:
        raise ManifestError("tab / window 打开方式需要 url")
    if "launch" in modes and not app["launch"]:
        raise ManifestError("launch 打开方式需要 launch.command")
    app["stop"] = _stop_spec(raw.get("stop"), app, path, command)
    return app


STOP_TYPES = {"wbkit", "command", "http", "close"}
_PROCESS_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- *")


def _process_name(value) -> str:
    name = _string(value, "process").strip()
    if not set(name) <= _PROCESS_CHARS or ("*" in name and (not name.endswith("*") or name.count("*") != 1)) or name == "*":
        raise ManifestError(f"进程名无效：{value!r}")
    return name


def _stop_spec(raw, app: dict, path, command) -> dict | None:
    health = app.get("health") or {}
    if raw is None:
        if health.get("type") == "runtime":
            raw = {"type": "wbkit"}
        elif health.get("type") == "process":
            raw = {"type": "close"}
        else:
            return None
    if raw is False:
        return None
    _object(raw, "stop")
    kind = raw.get("type")
    windows = raw.get("windows", [])
    if not isinstance(windows, list):
        raise ManifestError("stop.windows 须为进程名数组")
    allow_force = raw.get("allow_force", kind in {"wbkit", "close"})
    if type(allow_force) is not bool:
        raise ManifestError("stop.allow_force 须为布尔值")
    spec = {"type": kind, "windows": [_process_name(w) for w in windows],
            "confirm": _string(raw.get("confirm", ""), "stop.confirm", empty=True), "allow_force": allow_force}
    if kind == "wbkit":
        runtime = raw.get("runtime") or (app.get("url") or {}).get("runtime") or health.get("path")
        if not runtime:
            raise ManifestError("stop.type=wbkit 需要 runtime 文件")
        spec["runtime"] = path(runtime, "stop.runtime")
    elif kind == "command":
        spec["command"] = command(raw.get("command"), "stop.command")
        spec["cwd"] = path(raw.get("cwd", "."), "stop.cwd")
        spec["timeout"] = _integer(raw.get("timeout", 60), "stop.timeout", 5, 180)
    elif kind == "http":
        spec["url"] = _local_url(raw.get("url"))
        token = raw.get("token")
        if token is not None:
            _object(token, "stop.token")
            header = _string(token.get("header"), "stop.token.header")
            if not re.fullmatch(r"[A-Za-z0-9-]+", header):
                raise ManifestError("stop.token.header 无效")
            spec["token"] = {"url": _local_url(token.get("url")),
                             "field": _string(token.get("field", "token"), "stop.token.field"), "header": header}
    elif kind == "close":
        spec["process"] = _process_name(raw.get("process") or health.get("process"))
    else:
        raise ManifestError(f"未知的 stop.type：{kind}")
    return spec


def manifest_files() -> list[Path]:
    """兼容旧本机清单检查；正式发现入口为 registration.Registry。"""
    return sorted(APPS_DIR.glob("*.json")) + sorted(p for p in WORKBENCH_ROOT.glob("*/workbench.json") if p.parent != HERE)


def load_apps() -> tuple[dict[str, dict], list[dict]]:
    apps, errors = {}, []
    for path in manifest_files():
        try:
            app = validate(json.loads(path.read_text(encoding="utf-8-sig")), path)
            if app["id"] in apps:
                raise ManifestError(f"重复 id：{app['id']}")
            apps[app["id"]] = app
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append({"source": str(path), "error": str(exc)})
    return apps, errors


# ------------------------------------------------------------------ 状态
def _port_open(port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_json(url: str, timeout: float = 1.2) -> dict | None:
    request = urllib.request.Request(url, headers={"Host": urlparse(url).netloc})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            body = response.read(65536)
    except (OSError, ValueError):
        return None
    try:
        value = json.loads(body)
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


_process_cache: tuple[float, set[str]] = (0.0, set())


def running_process_names(fresh: bool = False) -> set[str]:
    global _process_cache
    if not fresh and time.monotonic() - _process_cache[0] < 2:
        return _process_cache[1]
    done = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, creationflags=NO_WINDOW, timeout=10)
    names = set()
    for line in done.stdout.decode("mbcs" if os.name == "nt" else "utf-8", "replace").splitlines():
        if line.startswith('"'):
            names.add(line.split('","', 1)[0].strip('"').casefold())
    _process_cache = (time.monotonic(), names)
    return names


def resolve_url(app: dict) -> str | None:
    if app.get("available") is False:
        return None
    url = app.get("url")
    if not url:
        return None
    if "fixed" in url:
        return url["fixed"]
    runtime = wbkit.read_runtime(Path(url["runtime"]))
    if runtime and runtime.get("url"):
        try:
            return _local_url(str(runtime["url"]))
        except ManifestError:
            return None
    return None


def check(app: dict) -> dict:
    if app.get("available") is False:
        return {"running": None, "url": None, "available": False}
    health = app.get("health")
    status = {"running": None, "url": None}
    if not health:
        return status
    kind = health["type"]
    if kind == "http":
        data = _http_json(health["url"])
        expect = health.get("expect_app")
        status["running"] = data is not None and (not expect or data.get("app") == expect)
    elif kind == "port":
        status["running"] = _port_open(health["port"])
    elif kind == "runtime":
        status["running"] = wbkit.read_runtime(Path(health["path"])) is not None
    elif kind == "process":
        # 结尾带 * 表示前缀匹配（进程名带版本号的情况）
        wanted = health["process"].casefold()
        try:
            names = running_process_names()
        except (OSError, subprocess.SubprocessError) as exc:
            return status | {"error": f"无法检查进程状态：{exc}"}
        status["running"] = (any(n.startswith(wanted[:-1]) for n in names) if wanted.endswith("*")
                             else wanted in names)
    if status["running"]:
        status["url"] = resolve_url(app)
    return status


def check_all(apps: dict[str, dict]) -> dict[str, dict]:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(zip(apps, pool.map(check, apps.values())))
    return results


# ------------------------------------------------------------------ 启动
def expand(parts: list[str]) -> list[str]:
    """替换命令里的 {python} {pythonw} {node} {powershell} 占位符。"""
    values = _placeholders()
    command = []
    for part in parts:
        for key, value in values.items():
            part = part.replace(key, value)
        command.append(part)
    return command


def build_command(app: dict) -> tuple[list[str], str]:
    if app.get("available") is False:
        raise ValueError(app.get("error") or "工作台当前不可用")
    if not app.get("launch"):
        raise ValueError(f"{app['name']} 没有启动命令")
    return expand(app["launch"]["command"]), app["launch"]["cwd"]


def launch(app: dict) -> int:
    command, cwd = build_command(app)
    exe = Path(command[0])
    if exe.is_absolute() and not exe.is_file():
        raise FileNotFoundError(f"找不到程序：{exe}")
    if not Path(cwd).is_dir():
        raise FileNotFoundError(f"工作目录不存在：{cwd}")
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, close_fds=True, creationflags=DETACHED)
    # 持有并回收 Popen 句柄，但不阻塞请求线程或限制子工作台的运行时长。
    threading.Thread(target=process.wait, name=f"fuuko-reap-{process.pid}", daemon=True).start()
    return process.pid


def wait_ready(app: dict, timeout: float = 25.0) -> dict:
    """启动后等待状态检查通过（用于标签页/窗口打开前取得地址）。"""
    deadline = time.monotonic() + timeout
    status = check(app)
    while not (status["running"] and (status["url"] or not app.get("url"))) and time.monotonic() < deadline:
        time.sleep(0.4)
        status = check(app)
    return status


# ------------------------------------------------------------------ 图标
ICON_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".jpg": "image/jpeg"}
_ICON_SCRIPT = r"""
Add-Type -AssemblyName System.Drawing
$icon = [System.Drawing.Icon]::ExtractAssociatedIcon($env:FUUKO_ICON_SRC)
$bitmap = $icon.ToBitmap()
$bitmap.Save($env:FUUKO_ICON_DST, [System.Drawing.Imaging.ImageFormat]::Png)
"""


def icon_file(app: dict, cache_dir: Path) -> Path | None:
    """返回可直接提供的图标文件；.exe/.dll 的图标提取为 PNG 并缓存。"""
    source = Path(app["icon"]) if app.get("icon") else None
    if not source or not source.is_file():
        return None
    suffix = source.suffix.lower()
    if suffix in ICON_TYPES:
        return source
    if suffix not in {".exe", ".dll"}:
        return None
    stat = source.stat()
    target = cache_dir / f"{app['id']}-{stat.st_size}-{int(stat.st_mtime)}.png"
    if not target.is_file():
        cache_dir.mkdir(parents=True, exist_ok=True)
        powershell = _placeholders()["{powershell}"]
        # 路径经子进程环境变量传入，避免拼进脚本文本
        env = dict(os.environ, FUUKO_ICON_SRC=str(source), FUUKO_ICON_DST=str(target))
        subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", _ICON_SCRIPT],
                       capture_output=True, timeout=20, creationflags=NO_WINDOW, env=env)
    return target if target.is_file() else None
