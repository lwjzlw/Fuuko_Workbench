"""本机工作台登记服务。扫描只读取清单，执行由 registry.launch 明确触发。"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path

import registry


class RegistryError(ValueError):
    pass


def _canonical(value: str | Path) -> str:
    if not isinstance(value, (str, Path)) or not str(value).strip() or "\x00" in str(value):
        raise RegistryError("路径须为非空字符串")
    return str(Path(value).expanduser().resolve())


def _key(value: str | Path) -> str:
    return os.path.normcase(_canonical(value))


def _json_read(path: Path, *, default: dict) -> dict:
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise RegistryError(f"文件过大：{path}")
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return copy.deepcopy(default)
    except (OSError, ValueError) as exc:
        raise RegistryError(f"无法读取 {path.name}，原文件已保留：{exc}") from exc
    if not isinstance(value, dict):
        raise RegistryError(f"{path.name} 须为 JSON 对象，原文件已保留")
    return value


def _signature(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _write_atomic(path: Path, value: dict) -> None:
    _write_bytes_atomic(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _write_bytes_atomic(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


_DISPLAY_FIELDS = ("id", "name", "version", "source", "schema_version", "legacy", "host_api", "repository",
                   "description", "category", "order", "accent", "note", "icon", "open", "data_dir")


class Registry:
    """持久化目录设置和登记身份；重新读取清单后才允许启动或关闭。"""

    def __init__(self, data_dir: str | Path, *, scan_roots=None, legacy_dirs=()):
        self.data_dir = Path(_canonical(data_dir))
        self.settings_path = self.data_dir / "settings.json"
        self.registry_path = self.data_dir / "registry.json"
        self._lock = threading.RLock()
        self._fault: str | None = None
        self._legacy_dirs = [_canonical(p) for p in legacy_dirs]
        defaults = {"scan_roots": [_canonical(p) for p in (scan_roots if scan_roots is not None else [self.data_dir / "workbenches"])],
                    "runtime_paths": {}, "data_dirs": {},
                    "workbench_data_root": str(registry.default_workbench_data_root())}
        settings = _json_read(self.settings_path, default=defaults)
        self._settings = self._validate_settings(settings, defaults)
        stored = _json_read(self.registry_path, default={"version": 1, "entries": {}, "excluded": []})
        if (type(stored.get("version")) is not int or stored["version"] != 1
                or not isinstance(stored.get("entries"), dict) or not isinstance(stored.get("excluded"), list)):
            raise RegistryError("registry.json 格式或版本无效，原文件已保留")
        self._entries = stored["entries"]
        for app_id, entry in self._entries.items():
            if (not isinstance(app_id, str) or not isinstance(entry, dict) or entry.get("id") != app_id
                    or not isinstance(entry.get("name"), str) or not isinstance(entry.get("source"), str)
                    or not entry["source"].strip() or not re.fullmatch(r"[A-Za-z0-9_-]+", app_id)):
                raise RegistryError("registry.json 的登记记录无效，原文件已保留")
            for field in ("category", "description", "accent", "note", "icon", "version", "repository", "data_dir"):
                if field in entry and not isinstance(entry[field], str):
                    raise RegistryError(f"registry.json 的 {field} 无效，原文件已保留")
            if "open" in entry and (not isinstance(entry["open"], list) or not entry["open"]
                                    or any(not isinstance(mode, str) or mode not in registry.OPEN_MODES for mode in entry["open"])):
                raise RegistryError("registry.json 的 open 无效，原文件已保留")
            if "order" in entry and type(entry["order"]) is not int:
                raise RegistryError("registry.json 的 order 无效，原文件已保留")
            entry["source"] = _canonical(entry["source"])
        if any(not isinstance(p, str) or not p.strip() for p in stored["excluded"]):
            raise RegistryError("registry.json 的排除路径无效，原文件已保留")
        self._excluded = {_key(p): _canonical(p) for p in stored["excluded"]}
        self._errors: list[dict] = []
        self._conflicts: list[dict] = []
        self._disk = {p: _signature(p) for p in (self.registry_path, self.settings_path)}

    @staticmethod
    def _validate_settings(body: dict, previous: dict) -> dict:
        if not isinstance(body, dict):
            raise RegistryError("设置须为对象")
        allowed = {"scan_roots", "runtime_paths", "data_dirs", "workbench_data_root"}
        if set(body) - allowed:
            raise RegistryError(f"未知的设置：{', '.join(sorted(set(body) - allowed))}")
        result = copy.deepcopy(previous)
        if "scan_roots" in body:
            roots = body["scan_roots"]
            if not isinstance(roots, list) or len(roots) > 100 or any(not isinstance(p, str) for p in roots):
                raise RegistryError("scan_roots 须为不超过 100 项的路径数组")
            result["scan_roots"] = list({_key(p): _canonical(p) for p in roots}.values())
        if "workbench_data_root" in body:
            result["workbench_data_root"] = _canonical(body["workbench_data_root"])
        for name in ("runtime_paths", "data_dirs"):
            if name not in body:
                continue
            values = body[name]
            if not isinstance(values, dict):
                raise RegistryError(f"{name} 须为对象")
            if name == "runtime_paths" and set(values) - {"python", "pythonw", "node", "powershell"}:
                raise RegistryError("runtime_paths 只允许 python、pythonw、node、powershell")
            cleaned = {}
            for key, value in values.items():
                if not isinstance(key, str) or not key or not isinstance(value, str) or not value.strip():
                    raise RegistryError(f"{name} 须为非空字符串到非空路径的映射")
                if name == "data_dirs" and not re.fullmatch(r"[A-Za-z0-9_-]+", key):
                    raise RegistryError("data_dirs 的 id 无效")
                cleaned[key] = _canonical(value)
            result[name] = cleaned
        return result

    def _assert_unchanged(self) -> None:
        if self._fault:
            raise RegistryError(self._fault)
        for path, signature in self._disk.items():
            if _signature(path) != signature:
                raise RegistryError(f"{path.name} 已被外部修改，请重新启动服务后再操作；未覆盖文件")

    def _save(self) -> None:
        self._assert_unchanged()
        value = {"version": 1, "entries": self._entries, "excluded": list(self._excluded.values())}
        serialized = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        if self.registry_path.exists() and self.registry_path.read_text(encoding="utf-8") == serialized:
            return
        _write_atomic(self.registry_path, value)
        self._disk[self.registry_path] = _signature(self.registry_path)

    def _read_app(self, source: str) -> dict:
        path = Path(source)
        if path.stat().st_size > 256 * 1024:
            raise RegistryError("说明文件超过 256 KiB")
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        app_id = raw.get("id") if isinstance(raw, dict) else None
        data_dir = None
        if isinstance(app_id, str):
            # 校验 id 后才用于拼路径；validate 仍负责完整协议检查。
            if not app_id or not all(c.isascii() and (c.isalnum() or c in "-_") for c in app_id):
                raise registry.ManifestError("id 只能由英文字母、数字、- 和 _ 组成")
            data_dir = self._settings["data_dirs"].get(app_id) or str(Path(self._settings["workbench_data_root"]) / app_id)
        return registry.validate(raw, path, data_dir=data_dir, runtime_paths=self._settings["runtime_paths"])

    @staticmethod
    def _record(app: dict) -> dict:
        return {key: copy.deepcopy(app[key]) for key in _DISPLAY_FIELDS} | {"state": "ready", "error": ""}

    def _revalidate(self) -> dict[str, dict]:
        apps = {}
        for app_id, entry in self._entries.items():
            try:
                app = self._read_app(entry["source"])
                if app["id"] != app_id:
                    raise RegistryError(f"说明文件的 id 已从 {app_id} 改成 {app['id']}，请取消旧登记后重新导入")
                entry.update(self._record(app))
                apps[app_id] = app
            except (OSError, ValueError, TypeError, KeyError) as exc:
                entry["state"] = "missing" if not Path(entry["source"]).is_file() else "invalid"
                entry["error"] = str(exc)
                # 不持久化、也不复用旧命令；不可用项只保留显示信息和身份。
                apps[app_id] = {
                    "id": app_id, "name": entry["name"], "category": "其他", "description": "", "accent": "",
                    "order": 100, "note": "", "icon": "", "open": ["launch"], "version": "", "legacy": True,
                    "repository": "", "schema_version": 0, "host_api": None, "data_dir": "",
                    **copy.deepcopy(entry), "available": False, "launch": None, "url": None, "health": None, "stop": None,
                }
        return apps

    def _discover(self) -> tuple[list[str], list[dict]]:
        paths, errors = {}, []
        for root_text in self._settings["scan_roots"]:
            root = Path(root_text)
            if not root.exists():
                continue
            if not root.is_dir():
                errors.append({"source": root_text, "error": "扫描根目录不是文件夹"})
                continue
            try:
                candidates = [root / "workbench.json"]
                candidates.extend(child / "workbench.json" for child in root.iterdir() if child.is_dir())
                for path in candidates:
                    if path.is_file():
                        paths[_key(path)] = _canonical(path)
            except OSError as exc:
                errors.append({"source": root_text, "error": str(exc)})
        for directory in self._legacy_dirs:
            try:
                for path in Path(directory).glob("*.json"):
                    paths[_key(path)] = _canonical(path)
            except OSError as exc:
                errors.append({"source": directory, "error": str(exc)})
        return sorted(paths.values(), key=_key), errors

    def scan(self) -> dict:
        with self._lock:
            self._assert_unchanged()
            old = copy.deepcopy(self._entries)
            self._revalidate()
            paths, errors = self._discover()
            candidates: dict[str, dict[str, dict]] = {}
            known_sources = {_key(entry["source"]): app_id for app_id, entry in self._entries.items()}
            # 手动登记的路径也参与冲突判断，即使不在扫描根目录内。
            for app_id, entry in self._entries.items():
                if entry["state"] == "ready":
                    candidates.setdefault(app_id, {})[_key(entry["source"])] = entry
            for source in paths:
                if _key(source) in self._excluded:
                    continue
                try:
                    app = self._read_app(source)
                    previous_id = known_sources.get(_key(source))
                    if previous_id is not None and previous_id != app["id"]:
                        raise RegistryError(f"该位置已登记为 {previous_id}，请先取消旧登记")
                    candidates.setdefault(app["id"], {})[_key(source)] = self._record(app)
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    errors.append({"source": source, "error": str(exc)})
            conflicts = []
            for app_id, group in sorted(candidates.items()):
                existing = self._entries.get(app_id)
                sources = [item["source"] for item in group.values()]
                if existing and _key(existing["source"]) not in group:
                    sources.insert(0, existing["source"])
                if len(sources) > 1:
                    conflicts.append({"id": app_id, "name": next(iter(group.values()))["name"],
                                      "sources": sources, "selected": existing["source"] if existing else ""})
                    continue
                if not existing:
                    self._entries[app_id] = next(iter(group.values()))
            self._errors, self._conflicts = errors, conflicts
            try:
                self._save()
            except (OSError, ValueError):
                self._entries = old
                raise
            snapshot = self._snapshot()
            snapshot["summary"] = {
                "added": sum(app_id not in old for app_id in self._entries),
                "updated": sum(app_id in old and old[app_id] != entry for app_id, entry in self._entries.items()),
                "unavailable": sum(entry["state"] != "ready" for entry in self._entries.values()),
                "conflicts": len(conflicts), "errors": len(snapshot["errors"]),
            }
            return snapshot

    def _snapshot(self) -> dict:
        errors = list(self._errors)
        seen = {(item["source"], item["error"]) for item in errors}
        for entry in self._entries.values():
            if entry.get("error") and (entry["source"], entry["error"]) not in seen:
                errors.append({"source": entry["source"], "error": entry["error"]})
        return copy.deepcopy({"entries": sorted(self._entries.values(), key=lambda e: e["id"]),
                              "errors": errors, "conflicts": self._conflicts,
                              "excluded": list(self._excluded.values()), "settings": self._settings})

    def snapshot(self) -> dict:
        with self._lock:
            self._revalidate()
            return self._snapshot()

    def apps(self) -> tuple[dict, list[dict]]:
        with self._lock:
            apps = self._revalidate()
            return apps, self._snapshot()["errors"]

    def get(self, app_id: str) -> dict:
        with self._lock:
            self._assert_unchanged()
            apps = self._revalidate()
            app = apps.get(app_id)
            if app is None:
                raise RegistryError(f"没有登记的工作台：{app_id}")
            if app.get("available") is False:
                raise RegistryError(f"工作台不可用：{app['error']}")
            return app

    def register(self, manifest_path: str | Path, replace: bool = False) -> dict:
        if type(replace) is not bool:
            raise RegistryError("replace 须为布尔值")
        with self._lock:
            self._assert_unchanged()
            source = _canonical(manifest_path)
            path = Path(source)
            if path.is_dir():
                source = str(path / "workbench.json")
            try:
                app = self._read_app(source)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise RegistryError(f"无法登记说明文件：{exc}") from exc
            existing = self._entries.get(app["id"])
            if existing and _key(existing["source"]) != _key(source) and not replace:
                raise RegistryError(f"id {app['id']} 已登记在 {existing['source']}；明确选择替换位置后再导入")
            for app_id, entry in self._entries.items():
                if app_id != app["id"] and _key(entry["source"]) == _key(source):
                    raise RegistryError(f"该位置已登记为 {app_id}，请先取消旧登记")
            old_entries, old_excluded = copy.deepcopy(self._entries), dict(self._excluded)
            self._entries[app["id"]] = self._record(app)
            self._excluded.pop(_key(source), None)
            try:
                self._save()
            except (OSError, ValueError):
                self._entries, self._excluded = old_entries, old_excluded
                raise
            return self.scan()

    def unregister(self, app_id: str) -> dict:
        if not isinstance(app_id, str) or not app_id:
            raise RegistryError("id 须为非空字符串")
        with self._lock:
            self._assert_unchanged()
            entry = self._entries.get(app_id)
            if entry is None:
                raise RegistryError(f"没有登记的工作台：{app_id}")
            # 同一 ID 的已发现位置一并排除，避免取消后另一副本自动接替。
            old_entries, old_excluded = copy.deepcopy(self._entries), dict(self._excluded)
            sources = [entry["source"]]
            for conflict in self._conflicts:
                if conflict["id"] == app_id:
                    sources.extend(conflict["sources"])
            for source in sources:
                self._excluded[_key(source)] = _canonical(source)
            del self._entries[app_id]
            try:
                self._save()
            except (OSError, ValueError):
                self._entries, self._excluded = old_entries, old_excluded
                raise
            return self.scan()

    def configure(self, body: dict) -> dict:
        with self._lock:
            self._assert_unchanged()
            next_settings = self._validate_settings(body, self._settings)
            previous_settings = copy.deepcopy(self._settings)
            previous_errors, previous_conflicts = copy.deepcopy(self._errors), copy.deepcopy(self._conflicts)
            previous_bytes = self.settings_path.read_bytes() if self.settings_path.exists() else None
            _write_atomic(self.settings_path, next_settings)
            self._settings = next_settings
            self._disk[self.settings_path] = _signature(self.settings_path)
            try:
                return self.scan()
            except (OSError, ValueError) as scan_error:
                try:
                    # 仅恢复本次写入的文件；外部写入的内容不得被回滚覆盖。
                    if _signature(self.settings_path) != self._disk[self.settings_path]:
                        raise RegistryError("settings.json 已被外部修改")
                    if previous_bytes is None:
                        self.settings_path.unlink()
                    else:
                        _write_bytes_atomic(self.settings_path, previous_bytes)
                    self._settings = previous_settings
                    self._disk[self.settings_path] = _signature(self.settings_path)
                    self._errors, self._conflicts = previous_errors, previous_conflicts
                except (OSError, ValueError) as rollback_error:
                    self._fault = (f"设置后的扫描失败（{scan_error}），且无法恢复先前设置（{rollback_error}）；"
                                   "当前设置可能已更改，请检查设置文件并重启服务")
                    self._errors.append({"source": str(self.settings_path), "error": self._fault})
                    raise RegistryError(self._fault) from rollback_error
                raise RegistryError(f"设置后的扫描失败，已恢复先前设置：{scan_error}") from scan_error
