"""风子工作台：各工作台和常用工具的统一入口。

    pythonw fuuko_app.py          # 启动并打开窗口（已在运行则只打开窗口）
    python  fuuko_app.py --serve  # 只启动服务

固定监听 127.0.0.1:17800，各工作台的 CSP 据此允许被嵌入标签页。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent

import registry  # noqa: E402
from shared import wbkit  # noqa: E402
from registration import Registry  # noqa: E402
import control  # noqa: E402
import layout  # noqa: E402

VERSION = "0.5.0"


def local_inventory() -> bool:
    """旧安装的适配清单只存在本机，源码发行包不携带它们。"""
    return any((HERE / "apps").glob("*.json"))


def default_data_dir() -> Path:
    if os.environ.get("FUUKO_DATA"):
        return Path(os.environ["FUUKO_DATA"]).expanduser().resolve()
    # 原位升级沿用已有数据；新安装不依赖开发者盘符。
    legacy = Path(HERE.anchor) / "AppData" / "Fuuko"
    if local_inventory() and legacy.is_dir():
        return legacy
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "Fuuko"


DATA_DIR = default_data_dir()
RUNTIME = DATA_DIR / "runtime.json"
FAVORITES = DATA_DIR / "favorites.json"
LAYOUT = DATA_DIR / "layout.json"
# 框架从空状态开始，常用项由用户自己加入
DEFAULT_FAVORITES: list[str] = []
MAX_FAVORITES = 100
DEFAULT_PROFILE_NAME = "风子"
MAX_PROFILE_NAME = 12
MAX_AVATAR_BYTES = 2 << 20
PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def load_favorites(known: set[str], path: Path | None = None) -> list[str]:
    """读取常用列表（按加入顺序），只返回仍登记着的应用；文件缺失或损坏时用默认值。"""
    try:
        data = json.loads((path or FAVORITES).read_text(encoding="utf-8"))
        ids = data.get("ids") if isinstance(data, dict) else None
        if not isinstance(ids, list):
            raise ValueError("bad favorites")
    except (OSError, ValueError):
        ids = DEFAULT_FAVORITES
    return [i for i in dict.fromkeys(str(x) for x in ids) if i in known]


def save_favorites(ids: object, known: set[str], path: Path | None = None) -> list[str]:
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
        raise ValueError("ids 须为字符串数组")
    # 已不再登记的应用（说明文件被删）直接丢掉，不报错
    clean = [x for x in dict.fromkeys(ids) if x in known][:MAX_FAVORITES]
    wbkit.write_json_atomic(path or FAVORITES, {"ids": clean})
    return clean


def build_app(port: int = wbkit.HUB_PORT, *, data_dir: Path | None = None,
              scan_roots: list[Path] | None = None, legacy: bool = False) -> wbkit.WorkbenchApp:
    """构造独立实例。默认从空状态开始；只有明确传入 legacy=True 才读取本机旧版适配清单。"""
    use_legacy = bool(legacy) and local_inventory()
    data_dir = Path(data_dir or DATA_DIR).expanduser().resolve()
    # 默认扫描目录：数据目录下的 workbenches，以及风子所在目录的上一级（与风子并排放的工作台）。
    roots = scan_roots if scan_roots is not None else [data_dir / "workbenches", HERE.parent]
    catalog = Registry(data_dir, scan_roots=roots, legacy_dirs=[HERE / "apps"] if use_legacy else ())
    # 启动时不自动登记新发现的工作台：只有用户点“扫描”或手动导入才加入，框架从空状态开始。
    # 明确要求读取旧版适配清单（--legacy）时才在启动时扫描一次。
    if use_legacy:
        catalog.scan()
    legacy_host = HERE.parent / "Shared" / "host" / "bin" / "FuukoHost.exe"
    # 只为明确启用旧清单的原位安装识别旧窗口，不把旧 Shared 当成框架依赖。
    host_paths = (legacy_host,) if use_legacy and legacy_host.is_file() else ()
    favorites_file, layout_file = data_dir / "favorites.json", data_dir / "layout.json"
    # 风子工作台本身不被别处嵌入；可嵌入本机任意端口的工作台
    app = wbkit.WorkbenchApp("fuuko", "风子工作台", HERE / "ui", data_dir / "runtime.json", VERSION, port,
                             embed_origins=(), frame_src="http://127.0.0.1:* http://localhost:*")
    app.registry = catalog
    app.data_dir = data_dir
    app.favorites_file = favorites_file
    app.layout_file = layout_file

    def find(app_id: str) -> dict:
        return catalog.get(app_id) | {"_host_paths": host_paths}

    # 个人资料：显示名字与头像（连点头像五下打开的菜单）。只存在本机数据目录。
    profile_file, avatar_file = data_dir / "profile.json", data_dir / "profile" / "avatar.png"

    def read_profile() -> dict:
        try:
            value = json.loads(profile_file.read_text(encoding="utf-8"))
            name = value.get("name") if isinstance(value, dict) else None
        except (OSError, ValueError):
            name = None
        name = name if isinstance(name, str) and name.strip() else DEFAULT_PROFILE_NAME
        version = int(avatar_file.stat().st_mtime) if avatar_file.is_file() else 0
        return {"name": name, "custom_avatar": bool(version), "avatar_version": version}

    @app.get("/api/profile")
    def get_profile(_query):
        return {"ok": True, **read_profile()}

    @app.post("/api/profile")
    def set_profile(body):
        if "name" in body:
            name = body["name"]
            if name is None:
                profile_file.unlink(missing_ok=True)
            else:
                if not isinstance(name, str):
                    raise ValueError("名字须为文本")
                name = " ".join(name.split())
                if not 1 <= len(name) <= MAX_PROFILE_NAME:
                    raise ValueError(f"名字须为 1 到 {MAX_PROFILE_NAME} 个字")
                wbkit.write_json_atomic(profile_file, {"name": name})
        if body.get("reset_avatar") is True:
            avatar_file.unlink(missing_ok=True)
        elif "avatar" in body:
            data = body["avatar"]
            prefix = "data:image/png;base64,"
            if not isinstance(data, str) or not data.startswith(prefix):
                raise ValueError("头像须为 PNG 图片")
            try:
                raw = base64.b64decode(data[len(prefix):], validate=True)
            except ValueError as exc:
                raise ValueError("头像数据无法解码") from exc
            if not raw.startswith(PNG_SIGNATURE) or len(raw) > MAX_AVATAR_BYTES:
                raise ValueError("头像须为不超过 2 MB 的 PNG 图片")
            avatar_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = avatar_file.with_suffix(".tmp")
            try:
                temporary.write_bytes(raw)
                os.replace(temporary, avatar_file)
            finally:
                temporary.unlink(missing_ok=True)
        return {"ok": True, **read_profile()}

    @app.raw_get("/api/profile/avatar")
    def profile_avatar(handler, _query):
        if avatar_file.is_file():
            return handler.send_file(avatar_file)
        return handler.send_file(wbkit.SHARED_ASSETS / "fuuko-avatar.png")

    @app.get("/api/registry")
    def registered(_query):
        return {"ok": True, **catalog.snapshot()}

    @app.post("/api/registry/scan")
    def scan(_body):
        return {"ok": True, **catalog.scan()}

    @app.post("/api/registry/register")
    def register(body):
        if not isinstance(body.get("replace", False), bool):
            raise ValueError("replace 须为 true/false")
        catalog.register(body.get("manifest_path"), replace=body.get("replace", False))
        return {"ok": True, **catalog.snapshot()}

    @app.post("/api/registry/unregister")
    def unregister(body):
        catalog.unregister(body.get("id"))
        return {"ok": True, **catalog.snapshot()}

    @app.post("/api/registry/settings")
    def registry_settings(body):
        catalog.configure(body)
        return {"ok": True, **catalog.snapshot()}

    def statuses_for(apps):
        active = {key: value for key, value in apps.items() if value.get("available", True)}
        return {key: {"running": None, "url": None} for key in apps} | registry.check_all(active)

    @app.get("/api/apps")
    def list_apps(_query):
        apps, errors = catalog.apps()
        statuses = statuses_for(apps)
        categories, effective = layout.apply(layout.load(layout_file), apps)
        items = []
        for app_id, item in apps.items():
            stop = item.get("stop") or {}
            available = item.get("available", True)
            items.append({k: item[k] for k in ("id", "name", "description", "accent", "order", "note", "open")}
                         | effective[app_id]
                         | {"has_icon": bool(item.get("icon")), "status": statuses[app_id],
                            "available": available, "error": item.get("error", ""),
                            "version": item.get("version", ""), "legacy": item.get("legacy", False),
                            "launchable": available and bool(item.get("launch")), "stoppable": available and bool(stop),
                            "stop_type": stop.get("type", ""), "stop_confirm": stop.get("confirm", ""),
                            "allow_force": bool(stop.get("allow_force"))})
        items.sort(key=lambda a: (a["order"], a["name"]))
        return {"ok": True, "apps": items, "errors": errors, "favorites": load_favorites(set(apps), favorites_file),
                "categories": categories, "icons": list(layout.ICONS)}

    @app.post("/api/layout")
    def set_layout(body):
        """整体保存分类（顺序、名称、图标）和各入口的分类覆盖、隐藏。"""
        apps, _ = catalog.apps()
        saved = layout.save(layout_file, body, apps)
        categories, effective = layout.apply(saved, apps)
        return {"ok": True, "categories": categories, "apps": effective}

    @app.post("/api/stop")
    def stop_app(body):
        """关闭子工作台（正常关闭；force=true 为用户确认后的强制结束）。"""
        item = find(str(body.get("id") or ""))
        return control.stop(item, force=body.get("force") is True)

    @app.post("/api/restart")
    def restart_app(body):
        item = find(str(body.get("id") or ""))
        if not item.get("launch"):
            raise ValueError(f"{item['name']} 没有启动命令，不能重启")
        result = control.stop(item)
        if not result["stopped"]:
            return result | {"restarted": False}
        registry.launch(item)
        status = registry.wait_ready(item) if item.get("health") else {"running": None, "url": None}
        return {"ok": True, "stopped": True, "restarted": True, "url": status["url"],
                "message": f"已重启 {item['name']}" if status["running"] is not False else f"已重新启动 {item['name']}，但还没检测到它就绪"}

    @app.get("/api/windows")
    def windows(_query):
        """共用窗口程序打开的独立窗口，按端口对应到登记的入口（风子自己的窗口不列出）。"""
        apps, _ = catalog.apps()
        ports = {}
        for app_id, item in apps.items():
            url = registry.resolve_url(item) if item.get("available", True) else None
            if url:
                ports.setdefault(urlparse(url).port, app_id)
        own_port = app.server.server_port if app.server else app.port
        items = [w | {"app": ports.get(w["port"], "")} for w in control.host_windows(host_paths=host_paths) if w["port"] != own_port]
        return {"ok": True, "windows": items}

    @app.post("/api/windows/close")
    def close_window(body):
        pid = body.get("pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError("pid 须为正整数")
        return control.close_window(pid, force=body.get("force") is True, host_paths=host_paths)

    @app.post("/api/favorites")
    def set_favorites(body):
        """整体保存常用列表（顺序即显示顺序）。"""
        apps, _ = catalog.apps()
        return {"ok": True, "favorites": save_favorites(body.get("ids"), set(apps), favorites_file)}

    @app.get("/api/status")
    def status(_query):
        apps, _ = catalog.apps()
        return {"ok": True, "status": statuses_for(apps),
                "availability": {key: {"available": value.get("available", True), "error": value.get("error", "")}
                                 for key, value in apps.items()}}

    @app.post("/api/open")
    def open_app(body):
        """mode=tab：确保服务在运行并返回地址；mode=window：另开独立窗口；mode=launch：运行启动命令。"""
        item = find(str(body.get("id") or ""))
        mode = str(body.get("mode") or item["open"][0])
        if mode not in item["open"]:
            raise ValueError(f"{item['name']} 不支持这种打开方式：{mode}")
        if mode == "launch":
            pid = registry.launch(item)
            return {"ok": True, "mode": mode, "pid": pid}
        status = registry.check(item)
        started = False
        if not status["running"] or not status["url"]:
            if not item.get("launch"):
                raise ValueError(f"{item['name']} 未运行，且没有配置启动命令")
            registry.launch(item)
            started = True
            status = registry.wait_ready(item)
            if not status["url"]:
                raise ValueError(f"{item['name']} 启动后没有在 25 秒内就绪")
        if mode == "window":
            icon = registry.icon_file(item, data_dir / "icons")
            wbkit.open_app_window(status["url"], data_dir / "windows" / item["id"], app_id=item["id"],
                                  title=item["name"], icon=icon)
        return {"ok": True, "mode": mode, "url": status["url"], "started": started}

    @app.raw_get("/api/icon")
    def icon(handler, query):
        try:
            item = find((query.get("id") or [""])[0])
        except ValueError:
            return handler.send_error_json(404, "no app")
        path = registry.icon_file(item, data_dir / "icons")
        if not path:
            return handler.send_error_json(404, "no icon")
        return handler.send_file(path)

    @app.post("/api/reveal")
    def reveal(body):
        item = find(str(body.get("id") or ""))
        import subprocess
        subprocess.Popen(["explorer.exe", "/select,", item["source"]])
        return {"ok": True}

    return app


def open_hub_window(url: str, data_dir: Path | None = None) -> None:
    wbkit.open_app_window(url, (data_dir or DATA_DIR) / "window", app_id="fuuko", title="风子工作台",
                          icon=wbkit.DEFAULT_ICON, width=1360, height=900)


def running_service(data_dir: Path | None = None) -> dict | None:
    """复用同版本服务；升级时经身份和关闭密钥校验后请求正常退出。"""
    running = wbkit.read_runtime((data_dir or DATA_DIR) / "runtime.json")
    if not running:
        return None
    url = registry._local_url(str(running.get("url") or "")).rstrip("/")
    status, health = control._request(url + "/api/health", timeout=3)
    if status != 200 or health.get("app") != "fuuko" or health.get("pid") != running.get("pid"):
        raise ValueError("运行记录和实际风子服务不一致，请检查占用端口的程序后重试")
    if health.get("version") == VERSION:
        return running
    if not running.get("control"):
        raise ValueError("旧版风子服务不支持正常退出，请先关闭旧服务再启动新版")
    status, _ = control._request(url + "/api/shutdown", {}, {"X-WB-Control": running["control"]})
    if status != 200:
        raise ValueError("旧版风子服务暂时不能退出，请完成正在进行的操作后重试")
    for _ in range(100):
        if not wbkit.pid_alive(running["pid"]):
            return None
        time.sleep(0.1)
    raise ValueError("已请求旧版风子服务退出，请稍后重新启动")


def main() -> int:
    parser = argparse.ArgumentParser(description="风子工作台")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--data-dir", type=Path, help="用户数据目录")
    parser.add_argument("--port", type=int, default=wbkit.HUB_PORT, help="监听端口（默认 17800）")
    parser.add_argument("--legacy", action="store_true", help="读取源码目录 apps/ 下的本机旧版适配清单（默认不读取）")
    parser.add_argument("--no-legacy", action="store_true", help="兼容旧命令；现在默认就不读取旧版适配清单")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("端口须在 0 至 65535 之间")
    data_dir = (args.data_dir or DATA_DIR).expanduser().resolve()
    try:
        running = running_service(data_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if running:
        if not args.serve:
            open_hub_window(running["url"], data_dir)
        return 0
    try:
        app = build_app(args.port, data_dir=data_dir,
                        legacy=args.legacy and not args.no_legacy)
        url = app.start()
    except (OSError, ValueError) as exc:
        print(f"风子工作台启动失败：{exc}", file=sys.stderr)
        return 1
    if not args.serve:
        open_hub_window(url, data_dir)
    print(json.dumps({"app": "fuuko", "url": url}), flush=True)
    try:
        app.wait(idle_minutes=10)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
