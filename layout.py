"""风子工作台：分类和入口显示设置（用户自己调的，不改各工作台的说明文件）。

用户数据目录中的 layout.json：
  {"categories": [{"name": "本机工作台", "icon": "window"}, ...],   顺序即侧栏顺序
   "apps": {"<id>": {"category": "图像", "hidden": true}}}           只存和说明文件不同的部分
说明文件里用到、但列表里没有的分类会自动补在后面，入口不会因为分类被删而消失。
"""
from __future__ import annotations

import json
from pathlib import Path

from shared import wbkit

ICONS = ("window", "spark", "image", "music", "book", "tools", "grid", "star", "file", "activity", "play", "home")
DEFAULT_CATEGORIES = [
    {"name": "本机工作台", "icon": "window"},
    {"name": "AI", "icon": "spark"},
    {"name": "图像", "icon": "image"},
    {"name": "音视频", "icon": "music"},
    {"name": "Gal 工具", "icon": "book"},
    {"name": "系统与效率", "icon": "tools"},
]
MAX_CATEGORIES = 40
MAX_NAME = 20


def _name(value) -> str:
    name = " ".join(str(value or "").split())
    if not name or len(name) > MAX_NAME or any(ord(ch) < 32 for ch in name):
        raise ValueError(f"分类名须为 1–{MAX_NAME} 个字：{value!r}")
    return name


def _categories(raw) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > MAX_CATEGORIES:
        raise ValueError(f"categories 须为不超过 {MAX_CATEGORIES} 项的数组")
    result, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("分类须为 {name, icon}")
        name = _name(item.get("name"))
        if name.casefold() in seen:
            raise ValueError(f"分类重名：{name}")
        seen.add(name.casefold())
        icon = item.get("icon") or "grid"
        if icon not in ICONS:
            raise ValueError(f"未知的图标：{icon}")
        result.append({"name": name, "icon": icon})
    return result


def load(path: Path) -> dict:
    """读取设置；文件缺失或损坏时用默认分类、无覆盖。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        categories = _categories(data.get("categories"))
        apps = data.get("apps") if isinstance(data.get("apps"), dict) else {}
    except (OSError, ValueError, AttributeError):
        return {"categories": [dict(c) for c in DEFAULT_CATEGORIES], "apps": {}}
    clean = {}
    for app_id, value in apps.items():
        if isinstance(value, dict):
            entry = {}
            if isinstance(value.get("category"), str) and value["category"].strip():
                entry["category"] = value["category"].strip()
            if value.get("hidden") is True:
                entry["hidden"] = True
            if entry:
                clean[str(app_id)] = entry
    return {"categories": categories, "apps": clean}


def apply(layout: dict, apps: dict[str, dict]) -> tuple[list[dict], dict[str, dict]]:
    """返回 (分类列表, {id: {category, default_category, hidden}})；用到但未列出的分类补在末尾。"""
    categories = [dict(c) for c in layout["categories"]]
    known = {c["name"] for c in categories}
    effective = {}
    for app_id, app in sorted(apps.items(), key=lambda kv: (kv[1]["order"], kv[1]["name"])):
        override = layout["apps"].get(app_id, {})
        category = override.get("category") or app["category"]
        if category not in known:
            categories.append({"name": category, "icon": "grid", "derived": True})
            known.add(category)
        effective[app_id] = {"category": category, "default_category": app["category"],
                             "hidden": bool(override.get("hidden"))}
    return categories, effective


def save(path: Path, body: dict, apps: dict[str, dict]) -> dict:
    """整体保存；和说明文件相同的分类不存，已不登记的 id 丢掉。"""
    categories = _categories(body.get("categories"))
    names = {c["name"] for c in categories}
    raw_apps = body.get("apps", {})
    if not isinstance(raw_apps, dict):
        raise ValueError("apps 须为对象")
    clean = {}
    for app_id, value in raw_apps.items():
        if app_id not in apps:
            continue
        if not isinstance(value, dict):
            raise ValueError(f"{app_id} 的设置须为对象")
        entry = {}
        category = value.get("category")
        if category not in (None, ""):
            category = _name(category)
            if category not in names:
                raise ValueError(f"分类不存在：{category}")
            if category != apps[app_id]["category"]:
                entry["category"] = category
        hidden = value.get("hidden", False)
        if not isinstance(hidden, bool):
            raise ValueError("hidden 须为 true/false")
        if hidden:
            entry["hidden"] = True
        if entry:
            clean[app_id] = entry
    # 入口仍在说明文件分类里、而这个分类被删了：它会在读取时自动补回，这里不报错
    value = {"categories": categories, "apps": clean}
    wbkit.write_json_atomic(Path(path), value)
    return load(path)
