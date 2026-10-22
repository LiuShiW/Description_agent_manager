"""Codex 风格多角色工作台（M4）。

- 左侧：群聊 + 6 角色，点谁看谁的对话与历史
- 主区群聊：所有角色 + 用户的消息
- 角色视图：可单独给该角色下达指令（私聊），它单独回复
"""
import asyncio
import queue
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from src.pipeline import run_pipeline, run_direct
from src.tools.code_exec import WORKSPACE

st.set_page_config(page_title="AI 软件团队", layout="wide")

AGENTS = [
    ("pm", "产品经理", "🧑‍💼"),
    ("arch", "架构师", "🏗️"),
    ("backend", "后端开发", "⚙️"),
    ("frontend", "前端开发", "🎨"),
    ("reviewer", "代码评审", "🔍"),
    ("qa", "QA测试", "🧪"),
]
AGENT_INFO = {k: (name, emoji) for k, name, emoji in AGENTS}
SOURCE_TO_ROLE = {
    "ProductManager": "pm", "Architect": "arch", "BackendDev": "backend",
    "FrontendDev": "frontend", "Reviewer": "reviewer", "QA": "qa", "user": "user",
}
USER_AVATAR = "🙋"


def _init_state():
    defaults = {
        "log": [], "pending": None, "running": False,
        "view": "group", "ws": str(WORKSPACE), "dm_running": set(),
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    if "event_q" not in st.session_state:
        st.session_state.event_q = queue.Queue()
    if "answer_q" not in st.session_state:
        st.session_state.answer_q = queue.Queue()


def _worker(req, mode, event_q, answer_q):
    def on_event(kind, source, text):
        event_q.put((kind, source, text))

    async def ask(prompt, kind):
        event_q.put(("ask", kind, prompt))
        return await asyncio.to_thread(answer_q.get)

    try:
        asyncio.run(run_pipeline(req, mode, on_event, ask))
    except Exception as e:
        event_q.put(("error", "", f"{type(e).__name__}: {e}"))
    finally:
        event_q.put(("done", "", ""))


def _dm_worker(role, instruction, history, event_q):
    def on_event(kind, source, text):
        event_q.put((kind, source, text))

    try:
        asyncio.run(run_direct(role, instruction, history, on_event))
    except Exception as e:
        event_q.put(("error", "", f"{type(e).__name__}: {e}"))
    finally:
        event_q.put(("dm_done", role, ""))


def _add(kind, source, text, target=None):
    st.session_state.log.append(
        {"kind": kind, "source": source, "text": text, "target": target})


def _bubble(msg, show_thought=True):
    kind, source, text = msg["kind"], msg["source"], msg["text"]
    if kind == "stage":
        st.markdown(f"#### {text}")
        return
    if kind == "error":
        st.error(text)
        return
    if kind == "tool":
        with st.expander("🛠️ 工具动作", expanded=False):
            st.code(text, language=None)
        return
    if kind == "thought":
        if show_thought:
            with st.expander("💭 思考过程", expanded=False):
                st.caption(text)
        return
    if kind == "text":
        role = SOURCE_TO_ROLE.get(source, source)
        if role == "user":
            tgt = msg.get("target")
            label = f"发给 {AGENT_INFO[tgt][0]}" if tgt in AGENT_INFO else "群聊"
            with st.chat_message("user", avatar=USER_AVATAR):
                st.caption(label)
                st.markdown(text)
        else:
            name, emoji = AGENT_INFO.get(role, (source, "🤖"))
            with st.chat_message("assistant", avatar=emoji):
                st.markdown(f"**{name}**")
                st.markdown(text)


def _visible(role=None):
    for msg in st.session_state.log:
        if role is None:
            yield msg
        else:
            src_role = SOURCE_TO_ROLE.get(msg["source"], msg["source"])
            if src_role == role or msg.get("target") == role:
                yield msg


def _history_text(limit=20, max_chars=400):
    lines = []
    for m in st.session_state.log[-limit:]:
        if m["kind"] != "text":
            continue
        role = SOURCE_TO_ROLE.get(m["source"], m["source"])
        name = "Leader" if role == "user" else AGENT_INFO.get(role, (role, ""))[0]
        text = m["text"]
        if len(text) > max_chars:
            text = text[:max_chars] + "…"
        lines.append(f"{name}: {text}")
    return "\n".join(lines)


def _list_ws_files():
    ws = Path(st.session_state.ws)
    if not ws.exists():
        return []
    return [str(x.relative_to(ws)) for x in sorted(ws.rglob("*"))
            if ".git" not in x.parts and x.is_file()]


_init_state()

# ===== 1. 主线程拉取后台事件（渲染前）=====
while not st.session_state.event_q.empty():
    ev = st.session_state.event_q.get()
    if ev[0] == "ask":
        st.session_state.pending = (ev[1], ev[2])
    elif ev[0] == "done":
        st.session_state.running = False
    elif ev[0] == "dm_done":
        st.session_state.dm_running.discard(ev[1])
    else:
        _add(ev[0], ev[1], ev[2])

# ===== 2. 左侧角色列表 =====
with st.sidebar:
    st.markdown("## 👥 AI 软件团队")
    st.caption("点角色看其历史，并在底部单独下指令")
    options = ["group"] + [k for k, _, _ in AGENTS]
    labels = {"group": "💬 群聊（全体）"}
    labels.update({k: f"{emoji} {name}" for k, name, emoji in AGENTS})
    try:
        idx = options.index(st.session_state.view)
    except ValueError:
        idx = 0
    st.session_state.view = st.radio("视图", options, index=idx,
                                     format_func=lambda x: labels[x])
    mode = st.radio("协作模式", ["roundrobin", "selector"],
                    format_func=lambda m: "串行接力" if m == "roundrobin" else "自动协作",
                    horizontal=True)
    st.divider()
    if st.session_state.running:
        status = "🟢 流水线运行中"
    elif st.session_state.dm_running:
        status = "💬 角色私聊中"
    elif st.session_state.pending:
        status = "⏸ 等待你决定"
    else:
        status = "⚪ 空闲"
    st.caption(f"状态：{status}")
    if st.button("🆕 新会话", use_container_width=True):
        st.session_state.log = []
        st.session_state.pending = None
        st.session_state.running = False
        st.session_state.dm_running = set()
        st.session_state.event_q = queue.Queue()
        st.session_state.answer_q = queue.Queue()
        st.rerun()

# ===== 3. 主区标题 =====
if st.session_state.view == "group":
    st.title("💬 群聊")
    st.caption("需求直接提给产品经理；所有角色的发言都会出现在这里")
else:
    name, emoji = AGENT_INFO[st.session_state.view]
    st.title(f"{emoji} {name}")
    st.caption(f"{name} 的对话历史（可在底部单独下达指令）")

# ===== 4. 消息区 =====
if st.session_state.view == "group":
    if not st.session_state.log:
        st.info("在下方输入需求，直接提给产品经理。")
    for msg in _visible(None):
        _bubble(msg, show_thought=False)
else:
    msgs = list(_visible(st.session_state.view))
    if not msgs:
        st.info("该角色还没有发言。")
    for msg in msgs:
        _bubble(msg, show_thought=True)

# ===== 5. 交互区 =====
st.divider()

if st.session_state.pending:
    kind, prompt = st.session_state.pending
    if kind == "approve":
        st.markdown(f"**⏸ 需要你审批：** {prompt}")
        c1, c2 = st.columns(2)
        if c1.button("✅ 批准", key="approve_yes", type="primary", use_container_width=True):
            _add("text", "user", "✅ 批准")
            st.session_state.answer_q.put("y")
            st.session_state.pending = None
            st.rerun()
        if c2.button("❌ 驳回", key="approve_no", use_container_width=True):
            _add("text", "user", "❌ 驳回")
            st.session_state.answer_q.put("n")
            st.session_state.pending = None
            st.rerun()
    else:
        st.markdown(f"**⏸ 产品经理在问你：** {prompt}")
        with st.form("answer_form"):
            ans = st.text_input("你的答复（留空 = 按默认假设执行）", value="", key="answer_text")
            if st.form_submit_button("发送给产品经理"):
                _add("text", "user", ans.strip() or "按默认假设执行")
                st.session_state.answer_q.put(ans or "按默认假设执行")
                st.session_state.pending = None
                st.rerun()
elif st.session_state.view == "group":
    if st.session_state.running:
        st.caption("流水线正在工作，消息会实时出现…")
    elif st.session_state.dm_running:
        st.caption("有角色正在单独回复，请稍候…")
    else:
        req = st.chat_input("向产品经理提出需求…")
        if req:
            st.session_state.log = []
            _add("text", "user", req)
            st.session_state.pending = None
            st.session_state.running = True
            _eq, _aq = queue.Queue(), queue.Queue()
            st.session_state.event_q = _eq
            st.session_state.answer_q = _aq
            threading.Thread(target=_worker, args=(req, mode, _eq, _aq), daemon=True).start()
            st.rerun()
else:
    role = st.session_state.view
    name, _ = AGENT_INFO[role]
    if role in st.session_state.dm_running:
        st.caption(f"{name} 正在回复…")
    else:
        dm = st.chat_input(f"给 {name} 单独下达指令…")
        if dm:
            _add("text", "user", dm, target=role)
            st.session_state.dm_running.add(role)
            hist = _history_text()
            threading.Thread(target=_dm_worker,
                             args=(role, dm, hist, st.session_state.event_q),
                             daemon=True).start()
            st.rerun()

# ===== 6. 完成态产出 =====
if (not st.session_state.running) and st.session_state.log and not st.session_state.pending:
    files = _list_ws_files()
    if files:
        with st.expander("📦 产出文件", expanded=False):
            for f in files:
                st.code(f, language=None)

# ===== 7. 自动刷新 =====
if st.session_state.running and not st.session_state.pending:
    time.sleep(0.4)
    st.rerun()
if st.session_state.dm_running:
    time.sleep(0.4)
    st.rerun()
