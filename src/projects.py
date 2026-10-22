"""多项目管理：每个项目有独立工作目录、会话与用量文件。

项目结构：
- 默认项目：root = app_root()，workspace = app_root()/workspace
- 新项目：  root = app_root()/projects/<名称>，workspace = root/workspace
- session.json / usage.json 放在项目 root 下
"""
import json
import re
from pathlib import Path

from .paths import app_root

PROJECTS_ROOT = app_root() / "projects"
REGISTRY = app_root() / "projects.json"


def _load() -> dict:
    if REGISTRY.exists():
        try:
            data = json.loads(REGISTRY.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("items", [])
                data.setdefault("current", None)
                return data
        except Exception:
            pass
    return {"current": None, "items": []}


def _save(data: dict):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _sanitize(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]+', "_", name or "").strip()
    return name or "项目"


def ensure_default() -> dict:
    data = _load()
    if not data["items"]:
        root = app_root()
        workspace = root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        data["items"].append({
            "name": "默认项目", "root": str(root), "path": str(workspace),
        })
        data["current"] = "默认项目"
        _save(data)
    if not data.get("current") or not any(
            p.get("name") == data["current"] for p in data["items"]):
        data["current"] = data["items"][0]["name"]
        _save(data)
    return data


def list_projects() -> list:
    return ensure_default()["items"]


def current_project() -> dict:
    data = ensure_default()
    name = data.get("current")
    for p in data["items"]:
        if p.get("name") == name:
            return p
    return data["items"][0]


def current_project_root() -> Path:
    p = current_project()
    root = p.get("root")
    if root:
        return Path(root)
    if p.get("name") == "默认项目":
        return app_root()
    return Path(p["path"]).parent


def current_workspace() -> Path:
    p = current_project()
    ws = Path(p["path"])
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def current_session_path() -> Path:
    return current_project_root() / "session.json"


def current_usage_path() -> Path:
    return current_project_root() / "usage.json"


def create_project(name: str) -> dict:
    data = ensure_default()
    name = _sanitize(name)
    if any(p.get("name") == name for p in data["items"]):
        raise ValueError("项目已存在")
    root = PROJECTS_ROOT / name
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    item = {"name": name, "root": str(root), "path": str(workspace)}
    data["items"].append(item)
    data["current"] = name
    _save(data)
    return item


def select_project(name: str) -> dict:
    data = ensure_default()
    for p in data["items"]:
        if p.get("name") == name:
            data["current"] = name
            _save(data)
            return p
    raise ValueError("项目不存在")
