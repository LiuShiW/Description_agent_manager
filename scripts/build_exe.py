"""一键打包：备份用户数据 -> 清理 dist -> PyInstaller -> 恢复配置与数据。"""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "AI软件团队"
BACKUP = ROOT / "_dist_userdata_backup"
ITEMS = ["workspace", "projects", "projects.json", "session.json", "usage.json"]

USAGE_TEXT = """AI 软件团队 · 桌面版 使用说明

1. 双击 AI软件团队.exe 启动（_internal 目录必须与 exe 同级）。
2. 首次启动：填写统一 API Key；之后可在「⚙ 设置」里为每个角色单独配置模型 / Key / Base URL。
3. 左侧「项目」下拉可切换项目，「＋ 新建项目」创建独立工作目录（projects\\<名称>\\workspace）。
4. 产出代码在对应项目的 workspace 目录；测试脚本在 QA 完成后自动清理。
5. 会话历史按项目自动保存；「🆕 新会话」清空当前项目历史。
6. 只统计 Token 用量，不计算金额、不设预算上限。
7. 用量按项目存储在 usage.json，每次运行/对话一条记录；「📊 用量详情」查看本次、项目累计与最近运行。
8. 运行中可点「⏹ 停止」在阶段边界停止。
9. 若启动失败，查看同目录 error.log。
10. 分发前请清空 config\\models.yaml 的 api_key。
"""


def onexc(func, path, exc):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as e:
        print(f"[警告] 无法删除 {path}: {e}")


def rm(path: Path):
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, onexc=onexc)
    else:
        try:
            os.chmod(path, stat.S_IWRITE)
            path.unlink()
        except Exception as e:
            print(f"[警告] 无法删除 {path}: {e}")


def app_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq AI软件团队.exe"],
            capture_output=True, text=True, errors="replace")
        return "AI软件团队.exe" in (out.stdout or "")
    except Exception:
        return False


def backup_userdata():
    rm(BACKUP)
    BACKUP.mkdir(parents=True, exist_ok=True)
    for name in ITEMS:
        src = DIST / name
        if not src.exists():
            continue
        dst = BACKUP / name
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        print(f"[备份] {name}")


def restore_userdata():
    cfg_dst = DIST / "config"
    cfg_dst.mkdir(parents=True, exist_ok=True)
    for f in (ROOT / "config").glob("*"):
        if f.is_file():
            shutil.copy2(f, cfg_dst / f.name)

    if BACKUP.exists():
        for item in BACKUP.iterdir():
            target = DIST / item.name
            if item.is_dir():
                if target.exists():
                    rm(target)
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)

    (DIST / "使用说明.txt").write_text(USAGE_TEXT, encoding="utf-8")


def main() -> int:
    if app_running():
        print("检测到 AI软件团队.exe 正在运行，请先关闭程序后再打包。")
        return 1

    print(f"[项目] {ROOT}")
    backup_userdata()

    print("[清理] dist ...")
    rm(DIST)

    print("[构建] PyInstaller ...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "desktop_app.spec", "--noconfirm"],
        cwd=str(ROOT))
    if result.returncode != 0:
        print("[失败] PyInstaller 构建失败。")
        return result.returncode

    restore_userdata()
    print(f"[完成] {DIST / 'AI软件团队.exe'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
