"""可复用的多段式流水线，供 CLI、Streamlit 与桌面端共用。

on_event(kind, source, text)：kind ∈ {stage, text, tool, thought, done, error}
ask(prompt, kind) -> str：kind ∈ {answer, approve}；阻塞直到返回用户输入
stop_event：threading.Event，置位后在阶段边界停止流水线
"""
import asyncio
import shutil
import subprocess
from autogen_agentchat.messages import (TextMessage, ToolCallRequestEvent,
                                        ToolCallExecutionEvent, ThoughtEvent)
from src.models import make_client
from src.agents import build_agents
from src.projects import current_workspace
from src.teams import build_team, build_stage
from src.tools.code_exec import (code_tool, write_file_tool, list_files_tool,
                                 read_file_tool, hidden_subprocess_kwargs)

ARCH_DONE = "交付完成"
QA_DONE = "【全部完成】"


def _last_text(messages, agent_name: str) -> str:
    for m in reversed(messages):
        if getattr(m, "source", "") == agent_name and getattr(m, "content", "").strip():
            return m.content
    return ""


def _strip_markers(text: str) -> str:
    for mk in (ARCH_DONE, QA_DONE):
        text = text.replace(mk, "")
    return text.strip()


def _init_workspace() -> str:
    ws = current_workspace()
    ws.mkdir(parents=True, exist_ok=True)
    if not (ws / ".git").exists():
        subprocess.run(["git", "init"], cwd=str(ws), capture_output=True,
                       **hidden_subprocess_kwargs())
    return str(ws)


def _cleanup_test_artifacts():
    """QA 完成后清理测试脚本，不单独留存。"""
    ws = current_workspace()
    for d in ("tests", "qa"):
        p = ws / d
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
    for pat in ("*.test.js", "*-test.js", "test-*.js", "*_test.py", "test_*.py"):
        for f in ws.glob(pat):
            try:
                f.unlink()
            except Exception:
                pass


async def _consume(stream, on_event):
    """消费 run_stream：逐条回调事件，返回末尾的 TaskResult。"""
    result = None
    async for item in stream:
        if isinstance(item, TextMessage):
            # 跳过各阶段内部的 task 文本（source=user），避免群聊里出现巨大的内部提示
            if getattr(item, "source", "") != "user":
                on_event("text", getattr(item, "source", ""), item.content)
        elif isinstance(item, ToolCallRequestEvent):
            on_event("tool", getattr(item, "source", ""), "调用工具：" + str(item.content))
        elif isinstance(item, ToolCallExecutionEvent):
            on_event("tool", getattr(item, "source", ""), "工具结果：" + str(item.content))
        elif isinstance(item, ThoughtEvent):
            on_event("thought", getattr(item, "source", ""), item.content)
        if hasattr(item, "messages"):
            result = item
    return result


async def run_pipeline(requirement: str, mode: str = "roundrobin",
                       on_event=None, ask=None, stop_event=None):
    on_event = on_event or (lambda *a, **k: None)

    async def _default_ask(prompt, kind):
        return (await asyncio.to_thread(input, prompt + " ")).strip()
    ask = ask or _default_ask

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    selector_llm = make_client()
    agents = build_agents(dev_tools=[code_tool, write_file_tool],
                          inspect_tools=[list_files_tool, read_file_tool])
    ws = _init_workspace()

    def abort():
        on_event("stage", "", "已停止（Leader 取消）")
        on_event("done", "", ws)

    if stopped():
        abort()
        return

    # 阶段一：PM -> PRD
    on_event("stage", "", "阶段一：需求 -> PRD")
    r1 = await _consume(
        build_team([agents["pm"]], selector_llm, "roundrobin",
                   done_text=ARCH_DONE, max_messages=2).run_stream(task=requirement),
        on_event)
    prd_text = _strip_markers(_last_text(r1.messages, "ProductManager"))
    if stopped():
        abort()
        return

    # 阶段二：Leader 回答
    answers = (await ask("产品经理已产出 PRD，请回答其中的问题（回车=按默认假设）。", "answer")).strip()
    if stopped() or answers == "__STOP__":
        abort()
        return

    # 阶段三：架构师 -> 架构
    on_event("stage", "", "阶段三：架构设计")
    r3 = await _consume(
        build_team([agents["arch"]], selector_llm, "roundrobin",
                   done_text=ARCH_DONE, max_messages=2).run_stream(
            task=f"以下是产品经理的 PRD：\n{prd_text}\n\n"
                 f"Leader 对 PRD 问题的答复：{answers or '按默认假设执行'}\n\n"
                 f"请据此输出完整架构设计文档。"),
        on_event)
    arch_text = _strip_markers(_last_text(r3.messages, "Architect"))
    if stopped():
        abort()
        return

    # 阶段四：Leader 审批
    approve = (await ask("架构方案已生成，是否批准进入开发？", "approve")).strip().lower()
    if stopped() or approve == "__stop__":
        abort()
        return
    if approve not in ("y", "yes", "通过", "1"):
        on_event("stage", "", "已终止：Leader 未批准")
        on_event("done", "", ws)
        return

    handoff = (f"【PRD】\n{prd_text}\n\n"
               f"【Leader 答复】\n{answers or '按默认假设执行'}\n\n"
               f"【架构设计（已批准）】\n{arch_text}")
    base = f"{handoff}\n\n工作目录：{ws}"

    # 阶段五：串行 后端 -> 前端 -> 评审 -> QA
    if stopped():
        abort()
        return
    r_be = await _consume(build_stage(agents["backend"]).run_stream(
        task=f"{base}\n\n你是后端开发。不要探测环境，直接完成交付："
             f"若需后端代码用 write_file 写入工作目录；若无需后端，一句话说明即可。"), on_event)
    be_out = _last_text(r_be.messages, "BackendDev")
    if stopped():
        abort()
        return

    r_fe = await _consume(build_stage(agents["frontend"]).run_stream(
        task=f"{base}\n\n后端交付：\n{be_out}\n\n"
             f"你是前端开发。用 write_file 把前端代码写入工作目录（文件名按架构要求），"
             f"完成后用 list_files 确认。"), on_event)
    fe_out = _last_text(r_fe.messages, "FrontendDev")
    if stopped():
        abort()
        return

    r_rv = await _consume(build_stage(agents["reviewer"]).run_stream(
        task=f"{base}\n\n后端交付：\n{be_out}\n\n前端交付：\n{fe_out}\n\n"
             f"你是评审。用 list_files / read_file 查看工作目录代码，输出评审意见（通过/需要修改）。"), on_event)
    rv_out = _last_text(r_rv.messages, "Reviewer")
    if stopped():
        abort()
        return

    await _consume(build_stage(agents["qa"]).run_stream(
        task=f"{base}\n\n评审意见：\n{rv_out}\n\n"
             f"你是QA。用 list_files / read_file / run_cmd 对工作目录代码做测试，输出测试报告。"), on_event)
    if stopped():
        abort()
        return

    _cleanup_test_artifacts()
    on_event("stage", "", "已清理测试脚本（不保留）")
    on_event("stage", "", "全部完成")
    on_event("done", "", ws)


async def run_direct(role_key: str, instruction: str, history_text: str = "",
                     on_event=None):
    """单角色私聊：只让指定角色回复一条消息（独立于主流水线）。"""
    on_event = on_event or (lambda *a, **k: None)
    agents = build_agents(dev_tools=[code_tool, write_file_tool],
                          inspect_tools=[list_files_tool, read_file_tool])
    agent = agents[role_key]
    task = ("以下是团队群聊近期记录，供你理解上下文：\n"
            f"{history_text or '（暂无）'}\n\n"
            f"Leader 单独给你下达指令：{instruction}\n\n"
            "请直接回应并执行该指令。")
    await _consume(build_stage(agent).run_stream(task=task), on_event)
