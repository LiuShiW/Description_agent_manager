"""受控代码执行与文件查看工具。

- run_cmd：白名单命令执行（执行前自动 git 快照）
- write_file：把文本内容写入 workspace 文件
- list_files / read_file：只读查看 workspace 文件
所有异常都被捕获并作为文本返回，绝不抛异常打断 Agent 会话。
"""
import os
import re
import shlex
import subprocess
import datetime
from pathlib import Path
from autogen_core.tools import FunctionTool

from ..projects import current_workspace

# 开发常用命令白名单。故意不含 rm/del/rmdir 等删除类命令。
WHITELIST = {
    "git", "python", "python3", "node", "npm", "npx", "pip", "pytest",
    "pwd", "ls", "dir", "cd", "echo", "cat", "type", "find", "grep",
    "head", "tail", "wc", "mkdir", "touch", "cp", "copy", "mv", "move",
}

_SPLIT = re.compile(r"(&&|\|\||[;|])")


def hidden_subprocess_kwargs() -> dict:
    """Windows 下隐藏子进程控制台窗口（打包成 GUI 程序时避免黑窗闪出）。"""
    if os.name != "nt":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": 0x08000000, "startupinfo": si}  # CREATE_NO_WINDOW


def _run(*args, **kwargs):
    for k, v in hidden_subprocess_kwargs().items():
        kwargs.setdefault(k, v)
    return subprocess.run(*args, **kwargs)


def _first_words(command: str) -> list:
    words = []
    for seg in _SPLIT.split(command):
        seg = seg.strip()
        if not seg or seg in ("&&", "||", ";", "|"):
            continue
        try:
            parts = shlex.split(seg)
        except ValueError:
            parts = seg.split()
        if parts:
            words.append(parts[0].lower())
    return words


def _reject_reason(command: str) -> str | None:
    words = _first_words(command)
    if not words:
        return "空命令"
    bad = sorted({w for w in words if w not in WHITELIST})
    if bad:
        return f"[拒绝] 命令不在白名单: {', '.join(bad)}\n白名单: {sorted(WHITELIST)}"
    return None


def _resolve_ws(p: str) -> Path:
    base = current_workspace()
    if not p:
        return base
    path = Path(p)
    return path if path.is_absolute() else base / path


def _ensure_workspace(cwd: Path) -> str:
    cwd.mkdir(parents=True, exist_ok=True)
    if not (cwd / ".git").exists():
        _run(["git", "init"], cwd=str(cwd), capture_output=True, timeout=30)
    return str(cwd)


def _snapshot(cwd: str) -> str:
    try:
        _run(["git", "-C", cwd, "add", "-A"], capture_output=True, timeout=30)
        _run(
            ["git", "-C", cwd, "commit", "-m",
             f"snapshot {datetime.datetime.now():%Y%m%d-%H%M%S}"],
            capture_output=True, timeout=30,
        )
        return "[已 git 快照]\n"
    except Exception as e:
        return f"[快照跳过] {e}\n"


async def run_cmd(command: str, cwd: str = "") -> str:
    reason = _reject_reason(command)
    if reason:
        return reason
    target = _ensure_workspace(_resolve_ws(cwd))
    out = _snapshot(target)
    try:
        p = _run(command, shell=True, cwd=target, capture_output=True,
                 timeout=120, encoding="utf-8", errors="replace")
        return out + (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return out + "[超时] 命令超过 120 秒被终止"
    except Exception as e:
        return out + f"[错误] {e}"


async def write_file(path: str, content: str) -> str:
    p = _resolve_ws(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"[已写入] {p}（{len(content)} 字符）"
    except Exception as e:
        return f"[写入失败] {e}"


async def list_files(path: str = "") -> str:
    p = _resolve_ws(path)
    if not p.exists():
        return f"[不存在] {p}"
    if not p.is_dir():
        return str(p)
    items = []
    for x in sorted(p.rglob("*")):
        if ".git" in x.parts:
            continue
        rel = x.relative_to(p)
        items.append(str(rel) + ("/" if x.is_dir() else ""))
    return "\n".join(items) or "[空目录]"


async def read_file(path: str) -> str:
    p = _resolve_ws(path)
    if not p.exists() or p.is_dir():
        return f"[无法读取] {p}"
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"[错误] {e}"


code_tool = FunctionTool(
    run_cmd,
    name="run_cmd",
    description="在 workspace 执行白名单命令(git/python/npm/node/pytest/pip等)，执行前自动 git 快照",
)
write_file_tool = FunctionTool(
    write_file,
    name="write_file",
    description="把文本内容写入 workspace 下的文件（覆盖写，path 相对 workspace）",
)
list_files_tool = FunctionTool(list_files, name="list_files", description="列出 workspace 下的文件")
read_file_tool = FunctionTool(read_file, name="read_file", description="读取 workspace 下的文件内容")
