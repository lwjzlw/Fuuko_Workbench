"""公开说明文件与登记生命周期的隔离回归；不会运行已安装工作台。"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import registry
import registration
from registration import Registry, RegistryError


def manifest(app_id="demo", **changes):
    return {"schema_version": 1, "id": app_id, "name": "示例", "version": "1.0.0",
            "requires": {"host_api": 1},
            "launch": {"command": ["{python}", "main.py", "--data-dir", "{data_dir}"], "cwd": "."}, **changes}


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "app" / "workbench.json"
        self.source.parent.mkdir()

    def test_context_paths_are_consistent_for_start_status_and_stop(self):
        data = self.base / "user data"
        raw = manifest(icon="ui/icon.svg", url={"runtime": "{data_dir}/runtime.json"},
                       health={"type": "runtime", "path": "{data_dir}/runtime.json"}, open=["tab", "window"])
        app = registry.validate(raw, self.source, data_dir=data)
        self.assertEqual(app["launch"]["command"][-1], str(data))
        self.assertEqual(app["url"]["runtime"], str(data / "runtime.json"))
        self.assertEqual(app["health"]["path"], app["url"]["runtime"])
        self.assertEqual(app["stop"]["runtime"], app["url"]["runtime"])
        self.assertEqual(app["icon"], str(self.source.parent / "ui" / "icon.svg"))
        self.assertFalse(data.exists())

    def test_runtime_precedence_and_command_stop_context(self):
        venv_python = self.source.parent / ".venv" / "Scripts" / "python.exe"
        venv_python.parent.mkdir(parents=True)
        venv_python.touch()
        raw = manifest(stop={"type": "command", "command": ["{python}", "{app_dir}/stop.py", "{data_dir}"], "cwd": "{app_dir}"})
        app = registry.validate(raw, self.source, data_dir=self.base / "data")
        self.assertEqual(registry.build_command(app)[0][0], str(venv_python))
        self.assertEqual(app["stop"]["command"][0], str(venv_python))
        explicit = self.base / "other python.exe"
        override = registry.validate(raw, self.source, runtime_paths={"python": str(explicit)})
        self.assertEqual(registry.build_command(override)[0][0], str(explicit))
        self.assertEqual(override["stop"]["cwd"], str(self.source.parent))

    def test_legacy_remains_supported(self):
        app = registry.validate({"id": "old-app", "name": "旧程序", "launch": {"command": ["program.exe"]}}, self.source)
        self.assertTrue(app["legacy"])
        self.assertEqual(app["schema_version"], 0)
        self.assertEqual(app["open"], ["launch"])

    def test_url_without_health_infers_port_and_waits_for_real_availability(self):
        raw = manifest(url={"fixed": "http://127.0.0.1:12345/"}, open=["tab"])
        raw.pop("launch")
        app = registry.validate(raw, self.source)
        self.assertEqual(app["health"], {"type": "port", "port": 12345})
        with patch.object(registry, "_port_open", return_value=False):
            self.assertEqual(registry.check(app), {"running": False, "url": None})
        with patch.object(registry, "_port_open", return_value=True):
            self.assertEqual(registry.wait_ready(app, timeout=0), {"running": True, "url": raw["url"]["fixed"]})
        launch_only = registry.validate(manifest(), self.source)
        self.assertIsNone(launch_only["health"])
        self.assertEqual(registry.check(launch_only), {"running": None, "url": None})

    def test_runtime_url_without_health_infers_runtime_check(self):
        app = registry.validate(manifest(url={"runtime": "{data_dir}/runtime.json"}, open=["tab"]),
                                self.source, data_dir=self.base / "runtime-data")
        self.assertEqual(app["health"], {"type": "runtime", "path": app["url"]["runtime"]})
        with patch.object(registry.wbkit, "read_runtime", return_value=None):
            self.assertFalse(registry.check(app)["running"])
        with patch.object(registry.wbkit, "read_runtime", return_value={"url": "http://127.0.0.1:12345/", "pid": 1}):
            self.assertEqual(registry.wait_ready(app, timeout=0)["url"], "http://127.0.0.1:12345/")

    def test_new_versions_are_checked(self):
        for changes in ({"schema_version": 2}, {"schema_version": True}, {"schema_version": 0},
                        {"requires": {"host_api": 2}}, {"requires": {"host_api": True}},
                        {"requires": []}, {"version": "1.0"}, {"version": "01.0.0"}, {"version": "1.0.0-01"},
                        {"version": "1.0.0-a..b"}, {"id": "Upper"}):
            with self.subTest(changes=changes), self.assertRaises(registry.ManifestError):
                registry.validate(manifest(**changes), self.source)
        self.assertEqual(registry.validate(manifest(version="1.2.3-rc.1+build.7"), self.source)["version"], "1.2.3-rc.1+build.7")

    def test_malformed_fields_report_validation_errors(self):
        cases = [{"launch": []}, {"launch": {"command": "not-an-array"}}, {"launch": {"command": [""]}},
                 {"launch": {"command": ["x", 1]}}, {"launch": {"command": ["x\x00"]}}, {"url": []},
                 {"url": {"fixed": "http://127.0.0.1:1/", "runtime": "rt.json"}}, {"health": []},
                 {"health": {"type": "port", "port": True}}, {"health": {"type": "port", "port": 65536}},
                 {"health": {"type": "process", "process": "*"}}, {"open": "launch"},
                 {"open": ["launch", "magic"]}, {"open": []}, {"order": True}, {"order": "10"},
                 {"name": []}, {"description": {}}, {"icon": False}, {"stop": []}, {"stop": {"type": "close", "process": "x", "allow_force": "false"}},
                 {"stop": {"type": "http", "url": "http://127.0.0.1:1/", "token": []}},
                 {"repository": "https://user:secret@example.com/repo"}]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(registry.ManifestError):
                registry.validate(manifest(**changes), self.source)

    def test_local_url_rejects_remote_missing_port_and_credentials(self):
        for value in ("http://remote.test:1/", "https://127.0.0.1:1/", "http://127.0.0.1/",
                      "http://localhost:99999/", "http://user@localhost:80/", "http://[bad", None):
            with self.subTest(value=value), self.assertRaises(registry.ManifestError):
                registry.validate(manifest(url={"fixed": value}, open=["tab"]), self.source)

    def test_unavailable_app_cannot_build_or_check(self):
        app = registry.validate(manifest(), self.source) | {"available": False, "error": "missing"}
        with self.assertRaisesRegex(ValueError, "missing"):
            registry.build_command(app)
        with patch.object(registry, "_http_json", side_effect=AssertionError("must not probe")):
            self.assertEqual(registry.check(app), {"running": None, "url": None, "available": False})

    def test_one_unavailable_status_provider_does_not_break_other_apps(self):
        app = registry.validate(manifest(health={"type": "process", "process": "demo.exe"}), self.source)
        other = registry.validate(manifest("other"), self.source)
        with patch.object(registry, "running_process_names", side_effect=OSError("tasklist unavailable")):
            statuses = registry.check_all({"demo": app, "other": other})
        self.assertIn("error", statuses["demo"])
        self.assertIsNone(statuses["other"]["running"])

    def test_launch_returns_while_child_runs_then_reaps_its_handle(self):
        marker = self.base / "may-exit"
        code = ("import pathlib,sys,time\np=pathlib.Path(sys.argv[1])\nend=time.monotonic()+8\n"
                "while not p.exists() and time.monotonic()<end: time.sleep(.02)\n")
        app = registry.validate(manifest(launch={"command": [sys.executable, "-c", code, str(marker)]}), self.source)
        real_popen = registry.subprocess.Popen
        processes, wait_threads = [], []
        reaped = threading.Event()

        def create(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            wait = process.wait

            def observed_wait(*a, **kw):
                wait_threads.append(threading.current_thread())
                result = wait(*a, **kw)
                reaped.set()
                return result

            process.wait = observed_wait
            return process

        try:
            with patch.object(registry.subprocess, "Popen", side_effect=create):
                pid = registry.launch(app)
            self.assertEqual(pid, processes[0].pid)
            self.assertFalse(reaped.is_set())
            self.assertIsNone(processes[0].poll())
            marker.touch()
            self.assertTrue(reaped.wait(6), "child process was not reaped")
            self.assertEqual(processes[0].returncode, 0)
            self.assertTrue(wait_threads[0].daemon)
            self.assertIsNot(wait_threads[0], threading.current_thread())
        finally:
            marker.touch(exist_ok=True)
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "installed"
        self.root.mkdir()
        self.data = self.base / "hub-data"
        self.hub = Registry(self.data, scan_roots=[self.root])

    def write(self, folder="one", app_id="demo", **changes):
        path = self.root / folder / "workbench.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest(app_id, **changes)), encoding="utf-8")
        return path

    def test_empty_framework_and_scan_never_launch(self):
        self.assertEqual(self.hub.scan()["entries"], [])
        self.write()
        with patch.object(registry, "launch", side_effect=AssertionError("must not launch")), \
                patch.object(registry.subprocess, "Popen", side_effect=AssertionError("must not launch")), \
                patch.object(registry, "check", side_effect=AssertionError("must not probe")):
            snapshot = self.hub.scan()
        self.assertEqual(snapshot["summary"]["added"], 1)
        self.assertEqual(snapshot["entries"][0]["state"], "ready")
        self.assertEqual(self.hub.scan()["summary"]["added"], 0)
        self.assertFalse((self.root / "one" / "__pycache__").exists())

    def test_only_root_and_immediate_children_scanned(self):
        self.write(".", "root")
        self.write("one", "child")
        nested = self.write("one/deep", "deep")
        self.assertEqual({a["id"] for a in self.hub.scan()["entries"]}, {"root", "child"})
        self.hub.register(nested)
        self.assertIn("deep", self.hub.apps()[0])

    def test_repeated_scan_and_register_are_idempotent(self):
        path = self.write()
        self.hub.scan()
        initial = (self.data / "registry.json").read_bytes()
        self.hub.register(path)
        self.hub.scan()
        self.assertEqual(initial, (self.data / "registry.json").read_bytes())
        restored = Registry(self.data, scan_roots=[])
        self.assertEqual(restored.get("demo")["source"], str(path))

    def test_scan_updates_version_without_new_identity(self):
        self.write()
        self.hub.scan()
        self.write(version="1.2.0", name="更新名称")
        result = self.hub.scan()
        self.assertEqual(result["summary"]["updated"], 1)
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(self.hub.get("demo")["version"], "1.2.0")

    def test_conflicts_do_not_choose_by_scan_order(self):
        first = self.write("a")
        second = self.write("b")
        result = self.hub.scan()
        self.assertEqual(result["entries"], [])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(set(result["conflicts"][0]["sources"]), {str(first), str(second)})
        self.hub.register(second, replace=True)
        self.assertEqual(self.hub.get("demo")["source"], str(second))
        self.assertEqual(self.hub.scan()["conflicts"][0]["selected"], str(second))
        with self.assertRaisesRegex(RegistryError, "已登记"):
            self.hub.register(first)
        self.hub.register(first, replace=True)
        self.assertEqual(self.hub.get("demo")["source"], str(first))

    def test_existing_registration_survives_new_conflict(self):
        first = self.write("one")
        self.hub.scan()
        self.write("two")
        self.assertEqual(len(self.hub.scan()["conflicts"]), 1)
        self.assertEqual(self.hub.get("demo")["source"], str(first))

    def test_missing_location_does_not_auto_switch_to_other_copy(self):
        first = self.write("one")
        self.hub.scan()
        first.unlink()
        self.write("two")
        result = self.hub.scan()
        self.assertEqual(result["entries"][0]["state"], "missing")
        self.assertEqual(len(result["conflicts"]), 1)
        with self.assertRaisesRegex(RegistryError, "不可用"):
            self.hub.get("demo")

    def test_missing_and_invalid_keep_identity_and_recover(self):
        path = self.write()
        self.hub.scan()
        path.unlink()
        self.assertEqual(self.hub.scan()["entries"][0]["state"], "missing")
        app = self.hub.apps()[0]["demo"]
        self.assertFalse(app["available"])
        self.assertIsNone(app["launch"])
        self.assertEqual(app["open"], ["launch"])
        self.write(version="2.0.0")
        self.assertEqual(self.hub.get("demo")["version"], "2.0.0")
        path.write_text("{invalid", encoding="utf-8")
        self.assertEqual(self.hub.snapshot()["entries"][0]["state"], "invalid")
        with self.assertRaises(RegistryError):
            self.hub.get("demo")
        self.write()
        self.assertTrue(self.hub.get("demo")["available"])

    def test_launch_revalidation_catches_identity_and_api_changes(self):
        self.write()
        self.hub.scan()
        self.write(app_id="changed")
        with self.assertRaisesRegex(RegistryError, "id 已从"):
            self.hub.get("demo")
        self.assertEqual({e["id"] for e in self.hub.scan()["entries"]}, {"demo"})
        self.write(requires={"host_api": 9})
        with self.assertRaisesRegex(RegistryError, "host_api"):
            self.hub.get("demo")

    def test_unregister_excludes_without_deleting_and_import_restores(self):
        path = self.write()
        user_file = path.parent / "user-data.txt"
        user_file.write_text("preserve", encoding="utf-8")
        self.hub.scan()
        result = self.hub.unregister("demo")
        self.assertEqual(result["entries"], [])
        self.assertIn(str(path), result["excluded"])
        self.assertEqual(self.hub.scan()["entries"], [])
        restored = Registry(self.data, scan_roots=[self.root])
        self.assertEqual(restored.scan()["entries"], [])
        restored.register(path)
        self.assertEqual(restored.snapshot()["excluded"], [])
        self.assertEqual(user_file.read_text(encoding="utf-8"), "preserve")

    def test_unregister_conflicting_copies_cannot_auto_replace(self):
        self.write("a")
        self.hub.scan()
        self.write("b")
        self.hub.scan()
        self.hub.unregister("demo")
        self.assertEqual(self.hub.scan()["entries"], [])
        self.assertEqual(len(self.hub.snapshot()["excluded"]), 2)

    def test_errors_isolated_from_valid_workbench(self):
        self.write("good", "good")
        bad = self.write("bad", "bad", schema_version=9)
        result = self.hub.scan()
        self.assertEqual([e["id"] for e in result["entries"]], ["good"])
        self.assertTrue(any(e["source"] == str(bad) for e in result["errors"]))

    def test_explicit_data_directory_and_runtime_settings_apply(self):
        self.write()
        data = self.base / "custom data"
        interpreter = self.base / "python.exe"
        self.hub.configure({"data_dirs": {"demo": str(data)}, "runtime_paths": {"python": str(interpreter)}})
        app = self.hub.get("demo")
        self.assertEqual(app["data_dir"], str(data))
        self.assertEqual(app["launch"]["command"][0], str(interpreter))
        self.assertFalse(data.exists())
        restored = Registry(self.data)
        self.assertEqual(restored.get("demo")["data_dir"], str(data))

    def test_bad_settings_do_not_write_or_change_config(self):
        initial = self.hub.snapshot()["settings"]
        for body in (None, {"scan_roots": "x"}, {"scan_roots": [1]}, {"scan_roots": [""]},
                     {"runtime_paths": {"unknown": "x"}}, {"runtime_paths": []}, {"data_dirs": {"a": None}},
                     {"data_dirs": {"../a": str(self.base)}},
                     {"unrecognized": 1}):
            with self.subTest(body=body), self.assertRaises(RegistryError):
                self.hub.configure(body)
        self.assertEqual(self.hub.snapshot()["settings"], initial)
        self.assertFalse(self.hub.settings_path.exists())

    def test_failed_configure_scan_restores_settings_bytes_and_fingerprint(self):
        self.write()
        self.hub.configure({"workbench_data_root": str(self.base / "original-data")})
        before = self.hub.settings_path.read_bytes()
        previous = self.hub.snapshot()["settings"]
        real_write = registration._write_atomic

        def refuse_registry(path, value):
            if path == self.hub.registry_path:
                raise PermissionError("registry write denied")
            return real_write(path, value)

        with patch("registration._write_atomic", side_effect=refuse_registry):
            with self.assertRaisesRegex(RegistryError, "已恢复先前设置"):
                self.hub.configure({"workbench_data_root": str(self.base / "new-data")})
        self.assertEqual(self.hub.settings_path.read_bytes(), before)
        self.assertEqual(self.hub.snapshot()["settings"], previous)
        self.assertEqual(self.hub.get("demo")["data_dir"], str(self.base / "original-data" / "demo"))
        self.assertEqual(list(self.data.glob("*.tmp")), [])

    def test_failed_first_configure_removes_only_its_new_settings_file(self):
        previous = self.hub.snapshot()["settings"]
        with patch.object(self.hub, "scan", side_effect=PermissionError("scan failed")):
            with self.assertRaisesRegex(RegistryError, "已恢复先前设置"):
                self.hub.configure({"scan_roots": []})
        self.assertFalse(self.hub.settings_path.exists())
        self.assertEqual(self.hub.snapshot()["settings"], previous)
        self.assertEqual(self.hub.scan()["entries"], [])

    def test_configure_rollback_does_not_overwrite_external_settings(self):
        foreign = b'{"scan_roots": []}'

        def external_write():
            self.hub.settings_path.write_bytes(foreign)
            raise PermissionError("scan failed")

        with patch.object(self.hub, "scan", side_effect=external_write):
            with self.assertRaisesRegex(RegistryError, "外部修改"):
                self.hub.configure({"scan_roots": [str(self.base / "new-root")]})
        self.assertEqual(self.hub.settings_path.read_bytes(), foreign)
        self.assertTrue(any("无法恢复" in item["error"] for item in self.hub.snapshot()["errors"]))
        with self.assertRaisesRegex(RegistryError, "当前设置可能已更改"):
            self.hub.get("demo")

    def test_configure_failed_rollback_reports_uncertain_state_and_stops_launch(self):
        self.hub.configure({"scan_roots": [str(self.root)]})
        real_write, calls = registration._write_bytes_atomic, []

        def fail_second_write(path, value):
            calls.append(path)
            if len(calls) == 2:
                raise PermissionError("rollback denied")
            return real_write(path, value)

        with patch("registration._write_bytes_atomic", side_effect=fail_second_write), \
                patch.object(self.hub, "scan", side_effect=PermissionError("scan failed")):
            with self.assertRaisesRegex(RegistryError, "当前设置可能已更改"):
                self.hub.configure({"scan_roots": []})
        self.assertEqual(json.loads(self.hub.settings_path.read_text(encoding="utf-8"))["scan_roots"], [])
        with self.assertRaisesRegex(RegistryError, "无法恢复"):
            self.hub.get("demo")

    def test_corrupted_persistence_is_preserved(self):
        self.data.mkdir()
        path = self.data / "registry.json"
        broken = b'{"broken":'
        path.write_bytes(broken)
        with self.assertRaisesRegex(RegistryError, "原文件已保留"):
            Registry(self.data)
        self.assertEqual(path.read_bytes(), broken)

    def test_external_changes_are_not_overwritten(self):
        self.write()
        self.hub.scan()
        self.hub.registry_path.write_text("changed elsewhere", encoding="utf-8")
        with self.assertRaisesRegex(RegistryError, "外部修改"):
            self.hub.scan()
        with self.assertRaises(RegistryError):
            self.hub.unregister("demo")
        self.assertEqual(self.hub.registry_path.read_text(encoding="utf-8"), "changed elsewhere")

    def test_legacy_adapters_require_explicit_directory(self):
        legacy = self.base / "legacy"
        legacy.mkdir()
        (legacy / "external.json").write_text(json.dumps({"id": "external", "name": "外部工具", "launch": {"command": ["external.exe"]}}), encoding="utf-8")
        self.assertEqual(self.hub.scan()["entries"], [])
        separate = Registry(self.base / "other-data", scan_roots=[], legacy_dirs=[legacy])
        self.assertTrue(separate.scan()["entries"][0]["legacy"])

    def test_atomic_write_failure_cleans_only_its_own_temp_file(self):
        self.write()
        with patch("registration.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                self.hub.scan()
        self.assertEqual(list(self.data.iterdir()), [])
        self.assertEqual(self.hub.snapshot()["entries"], [])

    def test_failed_unregister_keeps_entry_and_disk(self):
        self.write()
        self.hub.scan()
        original = self.hub.registry_path.read_bytes()
        with patch("registration.os.replace", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                self.hub.unregister("demo")
        self.assertEqual(self.hub.registry_path.read_bytes(), original)
        self.assertEqual(self.hub.get("demo")["id"], "demo")
        self.assertEqual(self.hub.snapshot()["excluded"], [])
        self.assertEqual(list(self.data.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
