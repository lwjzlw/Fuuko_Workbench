"""风子工作台：关闭子工作台、管理共用窗口程序打开的独立窗口。

只结束能确认身份的进程：
- wbkit：runtime 里的 pid 与 /api/health 返回的 app、pid 一致，才发关闭请求（凭 runtime 里的关闭密钥）。
  更新前启动、没有关闭密钥的服务，只有用户确认“强制结束”后才按这个 PID 结束，不带 /T，不连带它启动的程序。
- command / http：交给工作台自己的停止脚本或停止接口，它们会自行检查任务和身份。
- close：taskkill /IM 不带 /F，相当于点窗口的关闭按钮；强制结束需用户再确认。
- 独立窗口：只关路径与共用窗口程序 FuukoHost.exe 一致的进程。
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import registry
from shared import wbkit

NO_WINDOW = registry.NO_WINDOW
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机地址不走系统代理


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("mbcs" if os.name == "nt" else "latin-1", "replace")


def _tail(text: str, lines: int = 6) -> str:
    kept = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(kept[-lines:])


def _json(raw: bytes) -> dict:
    try:
        value = json.loads(raw or b"{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _request(url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 5.0) -> tuple[int, dict]:
    """请求本机接口，返回 (状态码, JSON)；连不上时状态码为 0。"""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="GET" if body is None else "POST")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, _json(response.read(1 << 20))
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, _json(exc.read(1 << 16))
    except (OSError, ValueError):
        return 0, {}


def _taskkill(args: list[str]) -> tuple[int, str]:
    done = subprocess.run(["taskkill", *args], capture_output=True, creationflags=NO_WINDOW, timeout=20)
    return done.returncode, _tail(_decode(done.stdout + done.stderr), 2)


def _wait(predicate, timeout: float, step: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(step)


def _not_running(app: dict) -> bool:
    if app["health"] and app["health"]["type"] == "process":
        registry.running_process_names(fresh=True)
    return registry.check(app)["running"] is not True


def _result(stopped: bool, message: str, **extra) -> dict:
    return {"ok": True, "stopped": stopped, "message": message, **extra}


# ------------------------------------------------------------------ 各种关闭方式
def _stop_wbkit(app: dict, spec: dict, force: bool) -> dict:
    runtime = wbkit.read_runtime(Path(spec["runtime"]))
    if not runtime:
        return _result(True, f"{app['name']} 没有在运行")
    url = registry._local_url(str(runtime.get("url") or "")).rstrip("/") + "/"
    pid = runtime["pid"]
    code, health = _request(url + "api/health", timeout=3)
    if code != 200 or health.get("pid") != pid or (runtime.get("app") and health.get("app") != runtime.get("app")):
        raise ValueError(f"{app['name']} 的运行记录和实际服务对不上，没有做任何操作")
    if runtime.get("control"):
        code, data = _request(url + "api/shutdown", {"force": bool(force)}, {"X-WB-Control": runtime["control"]}, timeout=10)
        if code == 409:
            return _result(False, data.get("error") or "还有任务在运行", busy=True, needs_force=spec["allow_force"])
        if code != 200:
            raise ValueError(f"{app['name']} 拒绝了关闭请求（HTTP {code}）{data.get('error', '')}")
        if _wait(lambda: not wbkit.pid_alive(pid), 20):
            return _result(True, f"已关闭 {app['name']}")
        return _result(False, f"已请求关闭 {app['name']}，但 20 秒内还没退出", needs_force=spec["allow_force"])
    # 更新前启动的服务没有关闭密钥：只能按已核对的 PID 强制结束
    if not force:
        return _result(False, f"{app['name']} 是更新前启动的，不支持正常关闭。可以强制结束它（PID {pid}），"
                              "正在进行的任务会中断；或者在它自己的窗口里关掉后等它自行退出。",
                       needs_force=spec["allow_force"], legacy=True)
    _taskkill(["/PID", str(pid), "/F"])
    if _wait(lambda: not wbkit.pid_alive(pid), 5):
        return _result(True, f"已强制结束 {app['name']}（PID {pid}）")
    raise ValueError(f"没能结束 {app['name']}（PID {pid}）")


def _stop_command(app: dict, spec: dict, force: bool) -> dict:
    command = registry.expand(spec["command"])
    exe = Path(command[0])
    if exe.is_absolute() and not exe.is_file():
        raise FileNotFoundError(f"找不到停止程序：{exe}")
    if not Path(spec["cwd"]).is_dir():
        raise FileNotFoundError(f"停止脚本的工作目录不存在：{spec['cwd']}")
    try:
        done = subprocess.run(command, cwd=spec["cwd"], stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=spec["timeout"], creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ValueError(f"{app['name']} 的停止脚本超过 {spec['timeout']} 秒没有结束") from None
    output = _tail(_decode(done.stdout + done.stderr))
    if done.returncode != 0:
        return _result(False, output or f"停止脚本失败（退出码 {done.returncode}）")
    if app["health"] and not _wait(lambda: _not_running(app), 15, 0.5):
        return _result(False, f"停止脚本已运行，但 {app['name']} 仍在运行。{output}")
    return _result(True, output or f"已关闭 {app['name']}")


def _stop_http(app: dict, spec: dict, force: bool) -> dict:
    headers = {}
    token = spec.get("token")
    if token:
        code, data = _request(token["url"])
        value = data.get(token["field"]) if code == 200 else None
        if not isinstance(value, str) or not value:
            raise ValueError(f"取不到 {app['name']} 的停止令牌（HTTP {code}）")
        headers[token["header"]] = value
    code, data = _request(spec["url"], {}, headers, timeout=20)
    if code != 200 or data.get("error") or data.get("ok") is False:
        return _result(False, str(data.get("error") or f"{app['name']} 拒绝了停止请求（HTTP {code}）"))
    if app["health"] and not _wait(lambda: _not_running(app), 20, 0.5):
        return _result(False, f"已请求停止，但 {app['name']} 20 秒内还没退出")
    return _result(True, f"已关闭 {app['name']}")


def _stop_close(app: dict, spec: dict, force: bool) -> dict:
    name = spec["process"]
    code, output = _taskkill((["/F"] if force else []) + ["/IM", name])
    if app["health"] and app["health"]["type"] == "process":
        if _wait(lambda: _not_running(app), 6 if not force else 3, 0.5):
            return _result(True, f"已{'强制结束' if force else '关闭'} {app['name']}")
        return _result(False, f"已请求关闭 {app['name']}，但它还开着（可能在问是否保存，或缩到了托盘）。",
                       needs_force=spec["allow_force"] and not force)
    if code != 0:
        return _result(False, output or f"没能关闭 {app['name']}", needs_force=spec["allow_force"] and not force)
    return _result(True, f"已请求关闭 {app['name']}")


STOPPERS = {"wbkit": _stop_wbkit, "command": _stop_command, "http": _stop_http, "close": _stop_close}


def stop(app: dict, force: bool = False) -> dict:
    """关闭一个子工作台；成功后再关掉显示它的独立窗口。返回 {stopped, message, needs_force?, windows_closed}。"""
    spec = app.get("stop")
    if not spec:
        raise ValueError(f"{app['name']} 不支持从风子工作台关闭")
    if force and not spec["allow_force"]:
        raise ValueError(f"{app['name']} 不支持强制结束")
    status = registry.check(app)
    if status["running"] is False:
        return _result(True, f"{app['name']} 没有在运行", windows_closed=0)
    url = status["url"] or registry.resolve_url(app)
    result = STOPPERS[spec["type"]](app, spec, force)
    result["windows_closed"] = close_windows_for(url, spec["windows"], host_paths=app.get("_host_paths", ())) if result["stopped"] else 0
    return result


# ------------------------------------------------------------------ 独立窗口
def _split_args(line: str) -> list[str]:
    if os.name != "nt":
        return line.split()
    shell32, kernel32 = ctypes.windll.shell32, ctypes.windll.kernel32
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    count = ctypes.c_int()
    argv = shell32.CommandLineToArgvW(line, ctypes.byref(count))
    if not argv:
        return []
    try:
        return [argv[i] for i in range(count.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))


_WINDOW_SCRIPT = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "@(Get-CimInstance Win32_Process -Filter \"Name='FuukoHost.exe'\" | "
    "Select-Object ProcessId, ExecutablePath, CommandLine) | ConvertTo-Json -Compress -Depth 2"
)


def host_windows(*, host_paths: tuple[Path, ...] = ()) -> list[dict]:
    """正在运行的共用窗口程序：[{pid, id, title, url, port}]（不含自检和截图用的实例）。"""
    if os.name != "nt":
        return []
    powershell = registry._placeholders()["{powershell}"]
    done = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", _WINDOW_SCRIPT],
                          capture_output=True, timeout=30, creationflags=NO_WINDOW)
    text = done.stdout.decode("utf-8", "replace").strip()
    try:
        rows = json.loads(text) if text else []
    except ValueError:
        rows = []
    if isinstance(rows, dict):
        rows = [rows]
    hosts = {os.path.normcase(str(path.resolve())) for path in (wbkit.HOST_EXE, *host_paths)}
    windows = []
    for row in rows:
        if not isinstance(row, dict) or os.path.normcase(str(row.get("ExecutablePath") or "")) not in hosts:
            continue
        args = _split_args(str(row.get("CommandLine") or ""))
        if "--self-test" in args or "--capture-dir" in args:
            continue
        options = {args[i]: args[i + 1] for i in range(1, len(args) - 1) if args[i].startswith("--")}
        url = options.get("--url", "")
        try:
            port = urlparse(url).port if url else None
        except ValueError:
            port = None
        windows.append({"pid": int(row["ProcessId"]), "id": options.get("--id", ""),
                        "title": options.get("--title", ""), "url": url, "port": port})
    windows.sort(key=lambda w: (w["title"], w["pid"]))
    return windows


def close_window(pid: int, force: bool = False, *, host_paths: tuple[Path, ...] = ()) -> dict:
    """关闭一个共用窗口程序的窗口；先核对它确实是 FuukoHost.exe。"""
    if not any(w["pid"] == pid for w in host_windows(host_paths=host_paths)):
        raise ValueError("这不是风子工作台的独立窗口，没有做任何操作")
    _taskkill(["/PID", str(pid)] + (["/F"] if force else []))
    if _wait(lambda: not wbkit.pid_alive(pid), 5):
        return {"ok": True, "closed": True}
    return {"ok": True, "closed": False, "message": "窗口还没关上，可以再试一次或强制关闭"}


def close_windows_for(url: str | None, process_names: list[str], *, host_paths: tuple[Path, ...] = ()) -> int:
    """服务停掉后，关掉显示它的独立窗口（按端口匹配，不碰风子自己的窗口）和说明文件列出的窗口进程。"""
    closed = 0
    try:
        port = urlparse(url).port if url else None
    except ValueError:
        port = None
    if port and port != wbkit.HUB_PORT:
        for window in host_windows(host_paths=host_paths):
            if window["port"] == port:
                closed += _taskkill(["/PID", str(window["pid"])])[0] == 0
    for name in process_names:
        closed += _taskkill(["/IM", name])[0] == 0
    return closed
