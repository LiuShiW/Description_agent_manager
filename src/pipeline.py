"""可复用的多段式流水线（自动判断项目类型）。

流程：
  需求 -> PM 出 PRD -> Leader 回答 -> 架构师出架构 -> Leader 审批
  -> 自动判断：纯后端 / 纯前端 / 前后端
     - 纯后端：后端 -> 评审 -> QA
     - 纯前端：前端 -> 评审 -> QA
     - 前后端：后端 ∥ 前端 -> 评审 -> QA
  -> 交付

on_event(kind, source, text)：kind ∈ {stage, text, tool, thought, usage, done, error}
ask(prompt, kind) -> str：kind ∈ {answer, approve}；阻塞直到返回用户输入
stop_event：threading.Event，置位后在阶段边界停止流水线
"""
import asyncio
import json
import re
import shutil
import subprocess

from autogen_agentchat.messages import (TextMessage, ToolCallRequestEvent,
                                        ToolCallExecutionEvent, ThoughtEvent)

from src.models import make_client
from src.agents import build_agents
from src.memory import ProjectMemory
from src.projects import current_workspace, current_usage_path, current_memory_path
from src.teams import build_team, build_stage
from src.tools.code_exec import (code_tool, write_file_tool, list_files_tool,
                                 read_file_tool, hidden_subprocess_kwargs)
from src.usage import UsageTracker, UsageStore

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


def _detect_project_type(architecture: str, prd: str, requirement: str) -> str:
    """返回 backend / frontend / fullstack。优先读取架构中的显式标记。"""
    m = re.search(r"项目类型\s*[:：]\s*(纯后端|纯前端|前后端)", architecture or "")
    if m:
        return {"纯后端": "backend", "纯前端": "frontend", "前后端": "fullstack"}[m.group(1)]

    text = f"{architecture}\n{prd}\n{requirement}"
    if any(k in text for k in ("纯后端", "无前端", "不需要前端", "仅后端", "没有前端", "命令行")):
        return "backend"
    if any(k in text for k in ("纯前端", "无后端", "不需要后端", "仅前端", "没有后端", "单文件HTML")):
        return "frontend"
    return "fullstack"


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


async def run_pipeline(requirement: str, on_event=None, ask=None,
                       stop_event=None, usage=None):
    on_event = on_event or (lambda *a, **k: None)

    async def _default_ask(prompt, kind):
        return (await asyncio.to_thread(input, prompt + " ")).strip()
    ask = ask or _default_ask

    usage = usage or UsageTracker()
    pipeline_llm = make_client(tracker=usage)
    agents = build_agents(dev_tools=[code_tool, write_file_tool],
                          inspect_tools=[list_files_tool, read_file_tool],
                          tracker=usage)
    ws = _init_workspace()
    memory = ProjectMemory(current_memory_path())
    memory.reset(requirement)
    store = UsageStore(current_usage_path())
    recorded = False
    run_mode = "pipeline"

    def record():
        nonlocal recorded
        if not recorded:
            store.append_run(usage.snapshot(), mode=run_mode, label=requirement)
            recorded = True

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def emit_usage():
        on_event("usage", "", json.dumps(usage.snapshot(), ensure_ascii=False))

    def abort():
        on_event("stage", "", "已停止（Leader 取消）")
        on_event("done", "", ws)

    def finish():
        _cleanup_test_artifacts()
        on_event("stage", "", "已清理测试脚本（不保留）")
        on_event("stage", "", "全部完成")
        on_event("done", "", ws)

    async def run_backend(parallel: bool = False):
        r = await _consume(build_stage(agents["backend"]).run_stream(
            task=f"{memory.context_for('backend')}\n\n工作目录：{ws}\n\n"
                 f"你是后端开发。不要探测环境，直接完成交付："
                 f"用 write_file 把后端代码写入工作目录；若本项目无需后端，一句话说明即可。"),
            on_event)
        out = _last_text(r.messages, "BackendDev")
        memory.set("backend", out)
        emit_usage()
        return out

    async def run_frontend(parallel: bool = False):
        # 并行时前端只依赖架构中的 API 契约，不等后端产出
        context = memory.context_for("frontend", parallel=parallel)
        r = await _consume(build_stage(agents["frontend"]).run_stream(
            task=f"{context}\n\n工作目录：{ws}\n\n"
                 f"你是前端开发。按架构中的 API 契约实现前端，用 write_file 写入工作目录；"
                 f"若本项目无需前端，一句话说明即可。"),
            on_event)
        out = _last_text(r.messages, "FrontendDev")
        memory.set("frontend", out)
        emit_usage()
        return out

    async def run_review():
        r = await _consume(build_stage(agents["reviewer"]).run_stream(
            task=f"{memory.context_for('reviewer')}\n\n工作目录：{ws}\n\n"
                 f"你是评审。用 list_files / read_file 查看工作目录代码，输出评审意见（通过/需要修改）。"),
            on_event)
        out = _last_text(r.messages, "Reviewer")
        memory.set("review", out)
        emit_usage()
        return out

    async def run_qa():
        r = await _consume(build_stage(agents["qa"]).run_stream(
            task=f"{memory.context_for('qa')}\n\n工作目录：{ws}\n\n"
                 f"你是QA。用 list_files / read_file / run_cmd 对工作目录代码做测试，输出测试报告。"),
            on_event)
        out = _last_text(r.messages, "QA")
        memory.set("qa", out)
        emit_usage()
        return out

    try:
        if stopped():
            abort()
            return

        # 阶段一：PM -> PRD
        on_event("stage", "", "阶段一：需求 -> PRD")
        r1 = await _consume(
            build_team([agents["pm"]], pipeline_llm, "roundrobin",
                       done_text=ARCH_DONE, max_messages=2).run_stream(task=requirement),
            on_event)
        prd_text = _strip_markers(_last_text(r1.messages, "ProductManager"))
        memory.set("prd", prd_text)
        emit_usage()
        if stopped():
            abort()
            return

        # 阶段二：Leader 回答
        answers = (await ask("产品经理已产出 PRD，请回答其中的问题（回车=按默认假设）。", "answer")).strip()
        if stopped() or answers == "__STOP__":
            abort()
            return

        # 阶段三：架构师 -> 架构
        memory.set("leader_answers", answers or "按默认假设执行")
        on_event("stage", "", "阶段三：架构设计")
        r3 = await _consume(
            build_team([agents["arch"]], pipeline_llm, "roundrobin",
                       done_text=ARCH_DONE, max_messages=2).run_stream(
                task=f"{memory.context_for('arch')}\n\n"
                     "请输出完整架构设计文档，并在文档开头单独一行输出：\n"
                     "项目类型：纯后端 / 纯前端 / 前后端\n"
                     "如果是前后端项目，必须给出明确的 API 契约。"),
            on_event)
        arch_text = _strip_markers(_last_text(r3.messages, "Architect"))
        memory.set("architecture", arch_text)
        emit_usage()
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

        # 阶段五：自动判断项目类型并开发
        ptype = _detect_project_type(arch_text, prd_text, requirement)
        run_mode = f"pipeline:{ptype}"
        label = {"backend": "纯后端", "frontend": "纯前端", "fullstack": "前后端并行"}[ptype]
        on_event("stage", "", f"阶段五：开发（{label}）")

        if ptype == "backend":
            await run_backend()
        elif ptype == "frontend":
            await run_frontend(parallel=False)
        else:
            # 前后端并行：接口契约来自架构，前端不等待后端产出
            await asyncio.gather(run_backend(), run_frontend(parallel=True))

        if stopped():
            abort()
            return

        # 阶段六：代码评审
        on_event("stage", "", "阶段六：代码评审")
        await run_review()
        if stopped():
            abort()
            return

        # 阶段七：QA 测试
        on_event("stage", "", "阶段七：QA 测试")
        await run_qa()
        if stopped():
            abort()
            return

        finish()
    finally:
        record()


async def run_direct(role_key: str, instruction: str, history_text: str = "",
                     on_event=None, usage=None):
    """单角色私聊：只让指定角色回复一条消息（独立于主流水线）。"""
    on_event = on_event or (lambda *a, **k: None)
    usage = usage or UsageTracker()
    store = UsageStore(current_usage_path())
    memory = ProjectMemory(current_memory_path())
    agents = build_agents(dev_tools=[code_tool, write_file_tool],
                          inspect_tools=[list_files_tool, read_file_tool],
                          tracker=usage)
    agent = agents[role_key]
    base = memory.context_for(role_key)
    task = (f"{base}\n\n近期群聊：\n{history_text or '（暂无）'}\n\n"
            f"Leader 单独给你下达指令：{instruction}\n\n"
            "请直接回应并执行该指令。")
    try:
        await _consume(build_stage(agent).run_stream(task=task), on_event)
        on_event("usage", "", json.dumps(usage.snapshot(), ensure_ascii=False))
    finally:
        store.append_run(usage.snapshot(), mode=f"direct:{role_key}", label=instruction)
