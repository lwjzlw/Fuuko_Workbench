"""框架登记接口及跨进程生命周期，全部使用隔离的临时目录。"""
from __future__ import annotations

import json
import os
import shutil
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

import fuuko_app
import registry
from shared import wbkit


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fuuko-api-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.apps_dir = self.root / "workbenches"
        self.apps_dir.mkdir()
        self.app = fuuko_app.build_app(port=0, data_dir=self.root / "data", scan_roots=[self.apps_dir], legacy=False)
        self.url = self.app.start().rstrip("/")
        self.addCleanup(self.app.stop)

    def call(self, path, body=None, *, token=True, headers=None):
        request = urllib.request.Request(self.url + path, method="POST" if body is not None else "GET",
                                         data=None if body is None else json.dumps(body).encode())
        if token:
            request.add_header("X-WB-Token", self.app.token)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                return response.status, json.loads(response.read() or b"{}"), response.headers
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.loads(exc.read() or b"{}"), exc.headers

    def manifest(self, name="demo", *, folder=None):
        directory = folder or self.apps_dir / name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "workbench.json"
        path.write_text(json.dumps({"schema_version": 1, "id": name, "name": name, "version": "1.0.0",
                                   "requires": {"host_api": 1}, "category": "工具", "open": ["launch"],
                                   "launch": {"command": ["{python}", "-c", "pass"]}}), encoding="utf-8")
        return path

    def scan(self):
        status, body, _ = self.call("/api/registry/scan", {})
        self.assertEqual(status, 200, body)
        return body

    def test_profile_name_and_avatar(self):
        import base64
        self.assertEqual(self.call("/api/profile")[1]["name"], "风子")
        code, body, _ = self.call("/api/profile", {"name": "  小 明  "})
        self.assertEqual((code, body["name"]), (200, "小 明"))
        for bad in [{"name": ""}, {"name": "x" * 13}, {"name": 3}, {"avatar": "data:image/jpeg;base64,AAAA"},
                    {"avatar": "data:image/png;base64,bm90IGEgcG5n"}, {"avatar": "data:image/png;base64,***"}]:
            with self.subTest(bad=bad):
                self.assertEqual(self.call("/api/profile", bad)[0], 400)
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
        code, body, _ = self.call("/api/profile", {"avatar": "data:image/png;base64," + base64.b64encode(png).decode()})
        self.assertEqual(code, 200, body)
        self.assertTrue(body["custom_avatar"])
        with urllib.request.urlopen(f"{self.url}/api/profile/avatar?token={self.app.token}", timeout=10) as response:
            self.assertEqual(response.read(), png)
        self.assertEqual(self.call("/api/profile", {"reset_avatar": True, "name": None})[1],
                         {"ok": True, "name": "风子", "custom_avatar": False, "avatar_version": 0})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"{self.url}/api/profile/avatar", timeout=10)
        caught.exception.close()
        self.assertEqual(caught.exception.code, 403)

    def test_empty_framework(self):
        code, data, headers = self.call("/api/apps")
        self.assertEqual((code, data["apps"], data["favorites"]), (200, [], []))
        self.assertIn("frame-src http://127.0.0.1:*", headers["Content-Security-Policy"])
        self.assertEqual(self.call("/api/registry")[1]["entries"], [])
        with urllib.request.urlopen(self.url + "/") as response:
            page = response.read().decode("utf-8")
        self.assertIn("/_shared/assets/fuuko-avatar.png", page)
        self.assertNotIn("{{WB_TOKEN}}", page)

    def test_scan_is_explicit_and_does_not_launch(self):
        self.manifest()
        with patch.object(registry, "launch", side_effect=AssertionError("scan must not launch")):
            self.assertEqual(self.call("/api/apps")[1]["apps"], [])
            self.scan()
            self.scan()
        apps = self.call("/api/apps")[1]["apps"]
        self.assertEqual([a["id"] for a in apps], ["demo"])
        self.assertTrue(apps[0]["available"])
        self.assertEqual(apps[0]["version"], "1.0.0")

    def test_registry_auth_and_origin(self):
        manifest = self.manifest()
        for path, body in [("/api/registry", None), ("/api/registry/scan", {}),
                           ("/api/registry/register", {"manifest_path": str(manifest)}),
                           ("/api/registry/unregister", {"id": "demo"}),
                           ("/api/registry/settings", {"scan_roots": []})]:
            with self.subTest(path=path):
                self.assertEqual(self.call(path, body, token=False)[0], 403)
        self.assertEqual(self.call("/api/registry/scan", {}, headers={"Origin": "https://example.invalid"})[0], 403)
        self.assertEqual(self.call("/api/registry")[1]["entries"], [])

    def test_import_unregister_and_manual_restore(self):
        manifest = self.manifest(folder=self.root / "outside")
        payload = {"manifest_path": str(manifest)}
        self.assertEqual(self.call("/api/registry/register", payload)[0], 200)
        self.assertEqual(self.call("/api/registry/unregister", {"id": "demo"})[0], 200)
        self.scan()
        self.assertEqual(self.call("/api/apps")[1]["apps"], [])
        self.assertTrue(manifest.is_file())
        self.assertEqual(self.call("/api/registry/register", payload)[0], 200)
        self.assertEqual(len(self.call("/api/apps")[1]["apps"]), 1)

    def test_duplicate_requires_explicit_selection(self):
        first = self.manifest(folder=self.apps_dir / "one")
        second = self.manifest(folder=self.apps_dir / "two")
        snap = self.scan()
        self.assertEqual(len(snap["conflicts"]), 1)
        self.assertEqual(self.call("/api/apps")[1]["apps"], [])
        self.assertEqual(self.call("/api/registry/register", {"manifest_path": str(first), "replace": True})[0], 200)
        self.assertEqual(self.call("/api/registry/register", {"manifest_path": str(second)})[0], 400)
        self.assertEqual(self.call("/api/registry/register", {"manifest_path": str(second), "replace": True})[0], 200)
        entries = self.call("/api/registry")[1]["entries"]
        self.assertEqual(Path(entries[0]["source"]), second)

    def test_missing_preserves_favorites_layout_and_blocks_actions(self):
        path = self.manifest()
        self.scan()
        self.call("/api/favorites", {"ids": ["demo"]})
        categories = self.call("/api/apps")[1]["categories"]
        self.call("/api/layout", {"categories": categories, "apps": {"demo": {"hidden": True}}})
        path.unlink()
        self.scan()
        result = self.call("/api/apps")[1]
        self.assertEqual(result["favorites"], ["demo"])
        self.assertTrue(result["apps"][0]["hidden"])
        self.assertFalse(result["apps"][0]["available"])
        for endpoint in ["open", "restart", "stop"]:
            self.assertEqual(self.call("/api/" + endpoint, {"id": "demo"})[0], 400)
        self.manifest()
        self.scan()
        result = self.call("/api/apps")[1]
        self.assertTrue(result["apps"][0]["available"])
        self.assertEqual(result["favorites"], ["demo"])

    def test_changed_manifest_revalidated_before_launch(self):
        path = self.manifest()
        self.scan()
        path.write_text("{}", encoding="utf-8")
        with patch.object(registry, "launch", side_effect=AssertionError("invalid app launched")):
            self.assertEqual(self.call("/api/open", {"id": "demo"})[0], 400)
        self.assertFalse(self.call("/api/status")[1]["availability"]["demo"]["available"])

    def test_settings_selects_roots_and_does_not_delete_apps(self):
        manifest = self.manifest(folder=self.root / "external")
        code, body, _ = self.call("/api/registry/settings", {"scan_roots": [str(manifest.parent)]})
        self.assertEqual(code, 200, body)
        self.assertEqual(len(self.call("/api/apps")[1]["apps"]), 1)
        self.assertEqual(self.call("/api/registry/settings", {"scan_roots": "bad"})[0], 400)
        self.assertTrue(manifest.exists())

    def test_favorites_layout_validation_and_unknown_id(self):
        self.manifest()
        self.scan()
        code, body, _ = self.call("/api/favorites", {"ids": ["demo", "demo", "unknown"]})
        self.assertEqual((code, body["favorites"]), (200, ["demo"]))
        for bad in [{"ids": "demo"}, {"ids": [1]}, {}]:
            self.assertEqual(self.call("/api/favorites", bad)[0], 400)
        self.assertEqual(self.call("/api/layout", {"categories": "bad"})[0], 400)
        self.assertEqual(self.call("/api/open", {"id": "unknown"})[0], 400)
        self.assertEqual(self.call("/api/open", {"id": "demo", "mode": "tab"})[0], 400)

    def test_static_only_exposes_public_resources(self):
        for path, expected in [("/_shared/fuuko.css", 200), ("/_shared/wbkit.js", 200),
                               ("/_shared/assets/fuuko-avatar.png", 200), ("/_shared/wbkit.py", 404),
                               ("/_shared/host/bin/FuukoHost.exe", 404), ("/_shared/assets/../wbkit.py", 404),
                               ("/_shared/assets/%2e%2e/wbkit.py", 404), ("/../fuuko_app.py", 404)]:
            with self.subTest(path=path):
                try:
                    with urllib.request.urlopen(self.url + path, timeout=10) as response:
                        status = response.status
                except urllib.error.HTTPError as exc:
                    with exc:
                        status = exc.code
                self.assertEqual(status, expected)

    def test_example_full_lifecycle(self):
        example = self.apps_dir / "hello"
        shutil.copytree(HERE / "examples" / "hello", example)
        self.call("/api/registry/settings", {"workbench_data_root": str(self.root / "example-data")})
        self.scan()
        app_id = json.loads((example / "workbench.json").read_text(encoding="utf-8"))["id"]
        pid = None
        try:
            code, result, _ = self.call("/api/open", {"id": app_id, "mode": "tab"})
            self.assertEqual(code, 200, result)
            with urllib.request.urlopen(result["url"] + "api/health", timeout=5) as response:
                health = json.loads(response.read())
            pid = health["pid"]
            self.assertNotEqual(pid, os.getpid())
            self.assertTrue(self.call("/api/status")[1]["status"][app_id]["running"])
            code, result, _ = self.call("/api/stop", {"id": app_id})
            self.assertEqual(code, 200, result)
            self.assertTrue(result["stopped"], result)
            self.assertFalse(wbkit.pid_alive(pid))
        finally:
            if pid and wbkit.pid_alive(pid):
                os.kill(pid, 9)
                for _ in range(50):
                    if not wbkit.pid_alive(pid):
                        break
                    time.sleep(0.1)


class ServiceUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fuuko-upgrade-")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)

    def spawn(self, version):
        code = ("import sys, fuuko_app as f; from pathlib import Path; f.VERSION=sys.argv[1]; "
                "app=f.build_app(port=0,data_dir=Path(sys.argv[2]),legacy=False); "
                "print(app.start(),flush=True); app.wait(idle_minutes=2); app.stop()")
        proc = subprocess.Popen([sys.executable, "-B", "-c", code, version, str(self.data)], cwd=HERE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        def cleanup():
            if proc.poll() is None:
                proc.kill()
            proc.wait(10)
            proc.stdout.close()
            proc.stderr.close()
        self.addCleanup(cleanup)
        self.assertTrue(proc.stdout.readline().startswith("http://127.0.0.1:"))
        return proc

    def test_same_service_reused_old_service_stops_normally(self):
        same = self.spawn(fuuko_app.VERSION)
        self.assertEqual(fuuko_app.running_service(self.data)["pid"], same.pid)
        same.kill()
        same.wait(10)
        old = self.spawn("0.0.1")
        self.assertIsNone(fuuko_app.running_service(self.data))
        self.assertEqual(old.wait(10), 0)

    def test_foreign_health_does_not_terminate_process(self):
        proc = self.spawn("0.0.1")
        with patch("control._request", return_value=(200, {"app": "other", "pid": proc.pid})):
            with self.assertRaises(ValueError):
                fuuko_app.running_service(self.data)
        self.assertIsNone(proc.poll())


if __name__ == "__main__":
    unittest.main()
