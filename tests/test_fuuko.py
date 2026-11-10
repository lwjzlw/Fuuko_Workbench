"""隔离验证窗口命令、关闭约定与布局；不启动本机业务工作台。"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
TEMP = None

import registry  # noqa: E402
import fuuko_app  # noqa: E402
from shared import wbkit  # noqa: E402
import control  # noqa: E402
import layout  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

SRC = None


def setUpModule():
    global TEMP, SRC
    TEMP = tempfile.TemporaryDirectory(prefix="fuuko-control-")
    SRC = Path(TEMP.name) / "x" / "workbench.json"
    unittest.addModuleCleanup(TEMP.cleanup)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WindowHostTests(unittest.TestCase):
    """独立窗口走共用 WebView2 窗口程序，不再调用 Edge。"""

    def test_host_built_with_avatar_icon(self):
        self.assertTrue(wbkit.DEFAULT_ICON.is_file(), wbkit.DEFAULT_ICON)
        self.assertTrue((wbkit.SHARED_ASSETS / "fuuko-avatar.png").is_file())

    def test_host_command(self):
        command = wbkit.host_command("http://127.0.0.1:17800/", Path(TEMP.name) / "w", app_id="fuuko",
                                     title="风子工作台", width=1360, height=900)
        self.assertEqual(command[0], str(wbkit.HOST_EXE))
        self.assertEqual(command[command.index("--url") + 1], "http://127.0.0.1:17800/")
        self.assertEqual(command[command.index("--icon") + 1], str(wbkit.DEFAULT_ICON))
        self.assertEqual(command[command.index("--profile") + 1], str(Path(TEMP.name) / "w" / "host"))
        self.assertNotIn("msedge", " ".join(command).lower())

    def test_no_edge_in_window_code(self):
        sources = [wbkit.SHARED_DIR / "wbkit.py", HERE / "fuuko_app.py"]
        for source in sources:
            with self.subTest(source=source.name):
                text = source.read_text(encoding="utf-8").lower()
                self.assertNotIn("msedge", text)
                self.assertNotIn("--app=", text)

    @unittest.skipUnless(wbkit.HOST_EXE.is_file(), "可选窗口程序尚未构建")
    def test_host_self_test(self):
        import subprocess
        report = Path(TEMP.name) / "host-selftest.json"
        subprocess.run([str(wbkit.HOST_EXE), "--self-test", "--report", str(report)], timeout=60, check=True)
        self.assertTrue(json.loads(report.read_text(encoding="utf-8"))["ok"])


class ManifestTests(unittest.TestCase):
    def test_minimal_launch_app(self):
        app = registry.validate({"id": "a", "name": "A", "launch": {"command": ["x.exe"]}}, SRC)
        self.assertEqual(app["open"], ["launch"])
        self.assertEqual(app["launch"]["cwd"], str(SRC.parent.resolve()))

    def test_rejects_bad_manifests(self):
        cases = [
            {"name": "A"},                                                   # 缺 id
            {"id": "a b", "name": "A"},                                      # id 非法
            {"id": "a", "name": "A", "open": ["tab"]},                       # tab 没有 url
            {"id": "a", "name": "A", "open": ["launch"]},                    # launch 没有命令
            {"id": "a", "name": "A", "url": {"fixed": "https://example.com/"}, "open": ["tab"]},  # 非本机
            {"id": "a", "name": "A", "url": {"fixed": "http://10.0.0.1:80/"}, "open": ["tab"]},
            {"id": "a", "name": "A", "launch": {"command": "x.exe"}},        # 命令须为数组
            {"id": "a", "name": "A", "launch": {"command": ["x"]}, "health": {"type": "magic"}},
        ]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises((ValueError, KeyError)):
                registry.validate(raw, SRC)

    def test_placeholders(self):
        app = registry.validate({"id": "a", "name": "A", "launch": {"command": ["{pythonw}", "x.py"]}}, SRC)
        command, _ = registry.build_command(app)
        self.assertTrue(command[0].lower().endswith(("pythonw.exe", "python.exe")))

    def test_runtime_and_process_checks(self):
        runtime = Path(TEMP.name) / "rt.json"
        app = registry.validate({"id": "r", "name": "R", "url": {"runtime": str(runtime)},
                                 "health": {"type": "runtime", "path": str(runtime)}, "open": ["tab"]}, SRC)
        self.assertEqual(registry.check(app), {"running": False, "url": None})
        wbkit.write_json_atomic(runtime, {"pid": os.getpid(), "url": "http://127.0.0.1:1234/"})
        self.assertEqual(registry.check(app), {"running": True, "url": "http://127.0.0.1:1234/"})
        wbkit.write_json_atomic(runtime, {"pid": os.getpid(), "url": "http://evil.example:80/"})
        self.assertIsNone(registry.check(app)["url"])  # runtime 里写了外部地址也不采用
        own = Path(sys.executable).name
        proc = registry.validate({"id": "p", "name": "P", "launch": {"command": ["x"]},
                                  "health": {"type": "process", "process": own[:4] + "*"}}, SRC)
        self.assertTrue(registry.check(proc)["running"])


class StopManifestTests(unittest.TestCase):
    def test_stop_inferred_from_health(self):
        runtime = registry.validate({"id": "r", "name": "R", "url": {"runtime": "rt.json"},
                                     "health": {"type": "runtime", "path": "rt.json"}, "open": ["tab"]}, SRC)
        self.assertEqual((runtime["stop"]["type"], runtime["stop"]["runtime"]), ("wbkit", str((SRC.parent / "rt.json").resolve())))
        proc = registry.validate({"id": "p", "name": "P", "launch": {"command": ["x"]},
                                  "health": {"type": "process", "process": "Foo.exe"}}, SRC)
        self.assertEqual((proc["stop"]["type"], proc["stop"]["process"], proc["stop"]["allow_force"]), ("close", "Foo.exe", True))
        port = registry.validate({"id": "q", "name": "Q", "launch": {"command": ["x"]}, "health": {"type": "port", "port": 1}}, SRC)
        self.assertIsNone(port["stop"])
        off = registry.validate({"id": "p", "name": "P", "launch": {"command": ["x"]}, "stop": False,
                                 "health": {"type": "process", "process": "Foo.exe"}}, SRC)
        self.assertIsNone(off["stop"])

    def test_rejects_bad_stop(self):
        base = {"id": "a", "name": "A", "launch": {"command": ["x"]}}
        for stop in ({"type": "magic"}, {"type": "command", "command": "x"}, {"type": "http", "url": "https://example.com/"},
                     {"type": "close"}, {"type": "close", "process": "a\\b.exe"}, {"type": "close", "process": "a*b"},
                     {"type": "close", "process": "a.exe", "windows": ["x\"y"]}, {"type": "wbkit"},
                     {"type": "http", "url": "http://127.0.0.1:1/", "token": {"url": "http://127.0.0.1:1/", "header": "a b"}}, "x"):
            with self.subTest(stop=stop), self.assertRaises((ValueError, KeyError, AttributeError)):
                registry.validate(base | {"stop": stop}, SRC)

class WbkitShutdownTests(unittest.TestCase):
    """wbkit 的关闭接口：只认 runtime 里的关闭密钥，不接受浏览器页面发起；有任务时默认拒绝。"""

    def test_shutdown_contract(self):
        runtime = Path(TEMP.name) / "wb-rt.json"
        app = wbkit.WorkbenchApp("t", "T", HERE / "ui", runtime, "1", free_port())
        url = app.start()
        waiter = threading.Thread(target=app.wait, daemon=True)
        waiter.start()
        saved = json.loads(runtime.read_text(encoding="utf-8"))
        self.assertEqual(saved["control"], app.control)
        self.assertNotEqual(app.control, app.token)

        def post(headers, body=b"{}"):
            request = urllib.request.Request(url + "api/shutdown", data=body, method="POST", headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as exc:
                with exc:
                    return exc.code, json.loads(exc.read() or b"{}")

        self.assertEqual(post({})[0], 403)
        self.assertEqual(post({"X-WB-Control": "wrong"})[0], 403)
        self.assertEqual(post({"X-WB-Token": app.token})[0], 403)  # 页面令牌不能用来关服务
        self.assertEqual(post({"X-WB-Control": app.control, "Origin": url.rstrip("/")})[0], 403)  # 浏览器发起的不接受
        release = threading.Event()
        app.jobs.submit("长任务", lambda job: release.wait(30))
        status, data = post({"X-WB-Control": app.control})
        self.assertEqual((status, data["busy"]), (409, ["长任务"]))
        self.assertTrue(waiter.is_alive())
        release.set()
        time.sleep(0.2)
        self.assertEqual(post({"X-WB-Control": app.control})[0], 200)
        waiter.join(10)
        self.assertFalse(waiter.is_alive())
        app.stop()
        self.assertFalse(runtime.exists())


CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[4])
from pathlib import Path
import wbkit
app = wbkit.WorkbenchApp("t-child", "测试", Path(sys.argv[2]), Path(sys.argv[1]), "1", 0)
if sys.argv[3] == "busy":
    app.jobs.submit("长任务", lambda job: time.sleep(60))
app.start()
print("ready", flush=True)
app.wait()
app.stop()
"""


class ControlStopTests(unittest.TestCase):
    def spawn(self, name: str, mode: str = ""):
        runtime = Path(TEMP.name) / f"{name}.json"
        proc = subprocess.Popen([sys.executable, "-c", CHILD, str(runtime), str(HERE / "ui"), mode or "-", str(wbkit.SHARED_DIR)],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        def finish():
            if proc.poll() is None:
                proc.kill()
            proc.wait(10)
            proc.stdout.close()
        self.addCleanup(finish)
        self.assertEqual(proc.stdout.readline().strip(), "ready")
        app = registry.validate({"id": name, "name": "测试服务", "url": {"runtime": str(runtime)},
                                 "health": {"type": "runtime", "path": str(runtime)}, "open": ["tab"]}, SRC)
        return proc, runtime, app

    def test_wbkit_graceful_stop(self):
        proc, runtime, app = self.spawn("child-ok")
        self.assertTrue(registry.check(app)["running"])
        result = control.stop(app)
        self.assertTrue(result["stopped"], result)
        self.assertEqual(proc.wait(10), 0)  # 正常退出，不是被结束
        self.assertFalse(runtime.exists())
        self.assertIn("没有在运行", control.stop(app)["message"])

    def test_wbkit_busy_needs_force(self):
        proc, _, app = self.spawn("child-busy", "busy")
        result = control.stop(app)
        self.assertEqual((result["stopped"], result.get("busy"), result.get("needs_force")), (False, True, True))
        self.assertIn("长任务", result["message"])
        self.assertIsNone(proc.poll())
        self.assertTrue(control.stop(app, force=True)["stopped"])
        self.assertEqual(proc.wait(10), 0)

    def test_legacy_service_only_killed_after_force(self):
        proc, runtime, app = self.spawn("child-legacy")
        saved = json.loads(runtime.read_text(encoding="utf-8"))
        saved.pop("control")  # 模拟更新前启动的服务
        wbkit.write_json_atomic(runtime, saved)
        result = control.stop(app)
        self.assertEqual((result["stopped"], result.get("needs_force"), result.get("legacy")), (False, True, True))
        self.assertIsNone(proc.poll())
        self.assertTrue(control.stop(app, force=True)["stopped"])
        self.assertIsNotNone(proc.wait(10))

    def test_mismatched_runtime_untouched(self):
        proc, runtime, app = self.spawn("child-mismatch")
        saved = json.loads(runtime.read_text(encoding="utf-8"))
        wbkit.write_json_atomic(runtime, saved | {"pid": os.getpid()})  # 记录的 PID 和服务对不上
        with self.assertRaises(ValueError):
            control.stop(app)
        self.assertIsNone(proc.poll())

    def test_command_stop(self):
        ok = registry.validate({"id": "c", "name": "C", "launch": {"command": ["x"]},
                                "stop": {"type": "command", "command": ["{python}", "-c", "print('stopped-ok')"], "cwd": TEMP.name}}, SRC)
        result = control.stop(ok)
        self.assertEqual((result["stopped"], result["message"]), (True, "stopped-ok"))
        bad = registry.validate({"id": "c", "name": "C", "launch": {"command": ["x"]}, "stop": {
            "type": "command", "command": ["{python}", "-c", "import sys; print('refused'); sys.exit(3)"], "cwd": TEMP.name}}, SRC)
        result = control.stop(bad)
        self.assertEqual((result["stopped"], result["message"]), (False, "refused"))
        missing = registry.validate({"id": "c", "name": "C", "launch": {"command": ["x"]},
                                     "stop": {"type": "command", "command": ["{python}"], "cwd": "no-such-dir"}}, SRC)
        with self.assertRaises(FileNotFoundError):
            control.stop(missing)

    def test_http_stop_with_token(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, value):
                body = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self.reply(200, {"token": "abc"})

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                calls.append(self.headers.get("X-Test-Token"))
                self.reply(200, {"ok": True}) if calls[-1] == "abc" else self.reply(403, {"error": "令牌不对"})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}/"
        good = registry.validate({"id": "h", "name": "H", "launch": {"command": ["x"]}, "stop": {
            "type": "http", "url": base + "stop", "token": {"url": base + "state", "field": "token", "header": "X-Test-Token"}}}, SRC)
        self.assertTrue(control.stop(good)["stopped"])
        wrong = registry.validate({"id": "h", "name": "H", "launch": {"command": ["x"]}, "stop": {
            "type": "http", "url": base + "stop"}}, SRC)
        result = control.stop(wrong)
        self.assertEqual((result["stopped"], result["message"]), (False, "令牌不对"))
        self.assertEqual(calls, ["abc", None])


class WindowTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows 窗口枚举")
    def test_legacy_host_requires_explicit_compatibility_path(self):
        legacy = Path(TEMP.name) / "legacy" / "FuukoHost.exe"
        unrelated = Path(TEMP.name) / "other" / "FuukoHost.exe"
        paths = [wbkit.HOST_EXE, legacy, unrelated]
        rows = [{"ProcessId": 100 + i, "ExecutablePath": str(path),
                 "CommandLine": f'"{path}" --url http://127.0.0.1:1234/ --id child'}
                for i, path in enumerate(paths)]
        result = subprocess.CompletedProcess([], 0, stdout=json.dumps(rows).encode("utf-8"))
        with patch.object(control.subprocess, "run", return_value=result):
            self.assertEqual([w["pid"] for w in control.host_windows()], [100])
            self.assertEqual([w["pid"] for w in control.host_windows(host_paths=(legacy,))], [100, 101])
            with self.assertRaises(ValueError):
                control.close_window(102, host_paths=(legacy,))

    def test_split_args(self):
        line = '"C:\\a b\\FuukoHost.exe" --url http://127.0.0.1:1/ --title "风子 工作台" --id x'
        self.assertEqual(control._split_args(line), ["C:\\a b\\FuukoHost.exe", "--url", "http://127.0.0.1:1/",
                                                     "--title", "风子 工作台", "--id", "x"])

    def test_host_windows_and_close_guard(self):
        windows = control.host_windows()
        self.assertIsInstance(windows, list)
        for window in windows:
            self.assertTrue({"pid", "id", "title", "url", "port"} <= set(window))
        with self.assertRaises(ValueError):
            control.close_window(os.getpid())  # 不是窗口程序的进程一律不碰


class LayoutTests(unittest.TestCase):
    APPS = {"a": {"category": "图像", "order": 1, "name": "A"}, "b": {"category": "自定义", "order": 2, "name": "B"}}

    def setUp(self):
        self.path = Path(TEMP.name) / "layout-test.json"
        self.path.unlink(missing_ok=True)

    def test_defaults_and_derived(self):
        saved = layout.load(self.path)
        self.assertEqual(saved, {"categories": layout.DEFAULT_CATEGORIES, "apps": {}})
        categories, effective = layout.apply(saved, self.APPS)
        self.assertEqual(categories[-1], {"name": "自定义", "icon": "grid", "derived": True})
        self.assertEqual(effective["a"], {"category": "图像", "default_category": "图像", "hidden": False})
        self.path.write_text("{oops", encoding="utf-8")
        self.assertEqual(layout.load(self.path)["apps"], {})

    def test_save_strips_defaults_and_unknown(self):
        cats = layout.DEFAULT_CATEGORIES + [{"name": "自定义", "icon": "star"}]
        saved = layout.save(self.path, {"categories": cats, "apps": {
            "a": {"category": "AI", "hidden": True}, "b": {"category": "自定义"}, "gone": {"category": "AI"}}}, self.APPS)
        self.assertEqual(saved["apps"], {"a": {"category": "AI", "hidden": True}})
        self.assertEqual(saved["categories"][-1], {"name": "自定义", "icon": "star"})
        _, effective = layout.apply(saved, self.APPS)
        self.assertEqual(effective["a"], {"category": "AI", "default_category": "图像", "hidden": True})

    def test_save_validation(self):
        cats = layout.DEFAULT_CATEGORIES
        bad = [
            {"categories": cats + [{"name": "ai", "icon": "grid"}], "apps": {}},          # 重名（不分大小写）
            {"categories": [{"name": "X", "icon": "rocket"}], "apps": {}},                # 未知图标
            {"categories": [{"name": "", "icon": "grid"}], "apps": {}},
            {"categories": [{"name": "长" * 21, "icon": "grid"}], "apps": {}},
            {"categories": cats, "apps": {"a": {"category": "不存在"}}},
            {"categories": cats, "apps": {"a": {"hidden": "yes"}}},
            {"categories": "x", "apps": {}},
            {"categories": cats, "apps": []},
        ]
        for body in bad:
            with self.subTest(body=str(body)[:60]), self.assertRaises(ValueError):
                layout.save(self.path, body, self.APPS)
        self.assertFalse(self.path.exists())  # 校验失败不落盘



if __name__ == "__main__":
    unittest.main()
