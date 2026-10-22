"""可复用的多段式流水线，供 CLI、Streamlit 与桌面端共用。

on_event(kind, source, text)：kind ∈ {stage, text, tool, thought, usage, done, error}
ask(prompt, kind) -> str：kind ∈ {answer, approve}；阻塞直到返回用户输入
stop_event：threading.Event，置位后在阶段边界停止流水线
usage：UsageTracker；每次运行结束会写入项目级 usage.json
"""
import asyncio
import json
import shutil
import subprocess
from autogen_agentchat.messages import (TextMessage, ToolCallRequestEvent,
                                        ToolCallExecutionEvent, ThoughtEvent)
from autogen_agentchat.conditions import TextMentionTermination, MaxMessageTermination
from autogen_agentchat.teams import SelectorGroupChat
from src.models import make_client
from src.agents import build_agents
from src.projects import current_workspace, current_usage_path, current_memory_path
from src.memory import ProjectMemory
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


SELECTOR_PROMPT = (
    "你是开发团队调度器。参与者：{roles}。\n"
    "阅读对话历史：\n{history}\n\n"
    "当前只允许从这些角色中选择：{participants}。\n"
    "请严格按候选顺序选择下一位，只返回一个角色名。"
)


def _selector_candidates(messages):
    """强制 SOP 顺序：后端 -> 前端 -> 评审 -> QA；全部说过后只允许 QA。"""
    order = ("BackendDev", "FrontendDev", "Reviewer", "QA")
    spoken = {getattr(m, "source", "") for m in messages if isinstance(m, TextMessage)}
    for name in order:
        if name not in spoken:
            return [name]
    return ["QA"]


async def run_pipeline(requirement: str, mode: str = "roundrobin",
                       on_event=None, ask=None, stop_event=None, usage=None):
    on_event = on_event or (lambda *a, **k: None)

    async def _default_ask(prompt, kind):
        return (await asyncio.to_thread(input, prompt + " ")).strip()
    ask = ask or _default_ask

    usage = usage or UsageTracker()
    selector_llm = make_client(tracker=usage)
    agents = build_agents(dev_tools=[code_tool, write_file_tool],
                          inspect_tools=[list_files_tool, read_file_tool],
                          tracker=usage)
    ws = _init_workspace()
    memory = ProjectMemory(current_memory_path())
    memory.reset(requirement)
    store = UsageStore(current_usage_path())
    recorded = False

    def record():
        nonlocal recorded
        if not recorded:
            store.append_run(usage.snapshot(), mode=mode, label=requirement)
            recorded = True

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def emit_usage():
        on_event("usage", "", json.dumps(usage.snapshot(), ensure_ascii=False))

    def abort():
        on_event("stage", "", "已停止（Leader 取消）")
        on_event("done", "", ws)

    try:
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
            build_team([agents["arch"]], selector_llm, "roundrobin",
                       done_text=ARCH_DONE, max_messages=2).run_stream(
                task=f"{memory.context_for('arch')}\n\n请据此输出完整架构设计文档。"),
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


        # 阶段五：自动协作（SOP 约束的 Selector）
        if mode == "selector":
            on_event("stage", "", "阶段五：自动协作（SOP 约束）")
            selector_task = (
                f"{memory.context_for('backend')}\n\n工作目录：{ws}\n\n"
                "你们是开发团队，请严格按顺序协作：\n"
                "1) 后端开发：完成交付；无需后端则说明\n"
                "2) 前端开发：在后端基础上完成前端\n"
                "3) 代码评审：用 list_files/read_file 评审\n"
                "4) QA：用 list_files/read_file/run_cmd 测试，最后一条消息以「【全部完成】」结尾\n"
                "前一步未完成不要跳到下一步。"
            )
            selector_team = SelectorGroupChat(
                [agents["backend"], agents["frontend"], agents["reviewer"], agents["qa"]],
                model_client=selector_llm,
                selector_prompt=SELECTOR_PROMPT,
                candidate_func=_selector_candidates,
                allow_repeated_speaker=True,
                max_turns=12,
                termination_condition=(
                    TextMentionTermination(QA_DONE) | MaxMessageTermination(20)
                ),
            )
            r_dev = await _consume(selector_team.run_stream(task=selector_task), on_event)
            for source, key in (("BackendDev", "backend"), ("FrontendDev", "frontend"),
                                ("Reviewer", "review"), ("QA", "qa")):
                memory.set(key, _last_text(r_dev.messages, source))
            emit_usage()
            if stopped():
                abort()
                return
            _cleanup_test_artifacts()
            on_event("stage", "", "已清理测试脚本（不保留）")
            on_event("stage", "", "全部完成")
            on_event("done", "", ws)
            return

        # 阶段五：串行 后端 -> 前端 -> 评审 -> QA
        if stopped():
            abort()
            return
        r_be = await _consume(build_stage(agents["backend"]).run_stream(
            task=f"{memory.context_for('backend')}\n\n工作目录：{ws}\n\n"
                 f"你是后端开发。不要探测环境，直接完成交付："
                 f"若需后端代码用 write_file 写入工作目录；若无需后端，一句话说明即可。"), on_event)
        be_out = _last_text(r_be.messages, "BackendDev")
        memory.set("backend", be_out)
        emit_usage()
        if stopped():
            abort()
            return

        r_fe = await _consume(build_stage(agents["frontend"]).run_stream(
            task=f"{memory.context_for('frontend')}\n\n工作目录：{ws}\n\n"
                 f"你是前端开发。用 write_file 把前端代码写入工作目录（文件名按架构要求），"
                 f"完成后用 list_files 确认。"), on_event)
        fe_out = _last_text(r_fe.messages, "FrontendDev")
        memory.set("frontend", fe_out)
        emit_usage()
        if stopped():
            abort()
            return

        r_rv = await _consume(build_stage(agents["reviewer"]).run_stream(
            task=f"{memory.context_for('reviewer')}\n\n工作目录：{ws}\n\n"
                 f"你是评审。用 list_files / read_file 查看工作目录代码，输出评审意见（通过/需要修改）。"), on_event)
        rv_out = _last_text(r_rv.messages, "Reviewer")
        memory.set("review", rv_out)
        emit_usage()
        if stopped():
            abort()
            return

        r_qa = await _consume(build_stage(agents["qa"]).run_stream(
            task=f"{memory.context_for('qa')}\n\n工作目录：{ws}\n\n"
                 f"你是QA。用 list_files / read_file / run_cmd 对工作目录代码做测试，输出测试报告。"), on_event)
        qa_out = _last_text(r_qa.messages, "QA")
        memory.set("qa", qa_out)
        emit_usage()
        if stopped():
            abort()
            return

        _cleanup_test_artifacts()
        on_event("stage", "", "已清理测试脚本（不保留）")
        on_event("stage", "", "全部完成")
        on_event("done", "", ws)
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
