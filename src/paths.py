"""路径解析：同时兼容开发运行与 PyInstaller 打包运行。"""
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """只读资源根：打包后是 _MEIPASS，开发时是项目根。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def app_root() -> Path:
    """可写目录根：打包后是 exe 所在目录，开发时是项目根。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def config_dir() -> Path:
    """优先使用 exe 旁的 config（便于改 key），不存在则用内置资源。"""
    external = app_root() / "config"
    if external.exists():
        return external
    return resource_root() / "config"


CONFIG_DIR = config_dir()
WORKSPACE = app_root() / "workspace"
