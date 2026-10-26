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
from pathlib import Path

from autogen_agentchat.messages import (TextMessage, ToolCallRequestEvent,
                                        ToolCallExecutionEvent, ThoughtEvent,
                                        HandoffMessage)
from autogen_core.models import UserMessage

from src.models import make_client
from src.agents import build_agents, DISPLAY_NAMES
from src.memory import ProjectMemory
from src.projects import current_workspace, current_usage_path, current_memory_path
from src.teams import build_team, build_stage
from src.tools.code_exec import (code_tool, write_file_tool, list_files_tool,
                                 read_file_tool, hidden_subprocess_kwargs)
from src.usage import UsageTracker, UsageStore

ARCH_DONE = "交付完成"
QA_DONE = "【全部完成】"

# 失败分类（在阻断分支产生，便于追踪与重试判断）
FAIL_USER_STOPPED = "user_stopped"
FAIL_APPROVAL_REJECTED = "approval_rejected"
FAIL_INTERNAL_ERROR = "internal_error"


def _last_text(messages, agent_name: str) -> str:
    for m in reversed(messages):
        if getattr(m, "source", "") == agent_name and getattr(m, "content", "").strip():
            return m.content
    return ""


def _strip_markers(text: str) -> str:
    for mk in (ARCH_DONE, QA_DONE):
        text = text.replace(mk, "")
    return text.strip()


def _extract_json(text: str) -> dict | None:
    """从 agent 文本提取 JSON 对象（代码块 / 平衡括号），失败返回 None。"""
    text = text or ""
    m = re.search(r"\`\`\`(?:json)?\s*(\{.*?\})\s*\`\`\`", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    start = text.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        pass
                    break
        start = text.find("{", start + 1)
    return None


def _detect_project_type(architecture: str, prd: str, requirement: str) -> tuple[str, str]:
    """返回 (backend/frontend/fullstack, declared|heuristic)。

    借鉴 Multica 的能力门思想：优先读结构化显式声明，声明缺失才用
    关键词启发式兜底，并把「来源」暴露给调用方（低置信度要能被看见）。
    """
    arch = architecture or ""
    # 1. 结构化 JSON 声明（最可信）
    m = re.search(r'["\']?project_type["\']?\s*[:=]\s*["\']?(backend|frontend|fullstack)["\']?',
                  arch, re.IGNORECASE)
    if m:
        return m.group(1).lower(), "declared"
    # 2. 中文显式标记
    m = re.search(r"项目类型\s*[:：]\s*(纯后端|纯前端|前后端)", arch)
    if m:
        return {"纯后端": "backend", "纯前端": "frontend", "前后端": "fullstack"}[m.group(1)], "declared"
    # 3. 关键词启发式兜底（低置信度）
    text = f"{arch}\n{prd}\n{requirement}"
    if any(k in text for k in ("纯后端", "无前端", "不需要前端", "仅后端", "没有前端", "命令行")):
        return "backend", "heuristic"
    if any(k in text for k in ("纯前端", "无后端", "不需要后端", "仅前端", "没有后端", "单文件HTML")):
        return "frontend", "heuristic"
    return "fullstack", "heuristic"


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
        elif isinstance(item, HandoffMessage):
            on_event("stage", "", f"转交：{getattr(item, 'source', '')} → {getattr(item, 'target', '')}")
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
        record()  # 先落盘再发事件
        on_event("error", "", f"[{FAIL_USER_STOPPED}] 已停止（Leader 取消）")
        on_event("done", "", ws)

    def finish():
        _cleanup_test_artifacts()
        record()  # 先落盘再发事件
        on_event("stage", "", "已清理测试脚本（不保留）")
        on_event("stage", "", "全部完成")
        on_event("done", "", ws)

    async def run_backend(parallel: bool = False):
        # 并行时后端写独立子目录，避免与前端在同一 workspace 冲突
        workdir = str(Path(ws) / "backend") if parallel else ws
        if parallel:
            (Path(ws) / "backend").mkdir(parents=True, exist_ok=True)
        r = await _consume(build_stage(agents["backend"]).run_stream(
            task=f"{memory.context_for('backend')}\n\n工作目录：{workdir}\n\n"
                 f"你是后端开发。不要探测环境，直接完成交付："
                 f"用 write_file 把后端代码写入工作目录（用上面的绝对路径）；若本项目无需后端，一句话说明即可。"),
            on_event)
        out = _last_text(r.messages, "BackendDev")
        memory.set("backend", out)
        emit_usage()
        return out

    async def run_frontend(parallel: bool = False):
        # 并行时前端写独立子目录，且只依赖架构中的 API 契约，不等后端产出
        workdir = str(Path(ws) / "frontend") if parallel else ws
        if parallel:
            (Path(ws) / "frontend").mkdir(parents=True, exist_ok=True)
        context = memory.context_for("frontend", parallel=parallel)
        r = await _consume(build_stage(agents["frontend"]).run_stream(
            task=f"{context}\n\n工作目录：{workdir}\n\n"
                 f"你是前端开发。按架构中的 API 契约实现前端，用 write_file 写入工作目录（用上面的绝对路径）；"
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
            record()  # 先落盘再发事件
            on_event("error", "", f"[{FAIL_APPROVAL_REJECTED}] Leader 未批准架构")
            on_event("done", "", ws)
            return

        # 阶段五：自动判断项目类型并开发
        ptype, type_source = _detect_project_type(arch_text, prd_text, requirement)
        run_mode = f"pipeline:{ptype}"
        label = {"backend": "纯后端", "frontend": "纯前端", "fullstack": "前后端并行"}[ptype]
        on_event("stage", "", f"阶段五：开发（{label}）")
        if type_source == "heuristic":
            on_event("stage", "", "提示：架构中无明确项目类型声明，已用启发式判断，请留意开发分支是否正确")

        if ptype == "backend":
            await run_backend()
        elif ptype == "frontend":
            await run_frontend(parallel=False)
        else:
            # 前后端并行：接口契约来自架构，前端不等待后端产出
            await asyncio.gather(run_backend(parallel=True), run_frontend(parallel=True))

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
    except Exception as e:
        record()  # 先落盘
        on_event("error", "", f"[{FAIL_INTERNAL_ERROR}] {type(e).__name__}: {e}")
        on_event("done", "", ws)
    finally:
        record()


async def _route_instruction(instruction: str, history_text: str, usage) -> dict:
    """轻量路由器：判断 Leader 指令归属哪个角色。

    返回 {"owner": role_key | "current", "reason": str}。
    owner 为 "current" 表示不转交（问询/讨论/路由失败）。
    """
    router = make_client(tracker=usage)
    prompt = (
        "你是任务路由器。判断下面 Leader 的指令应该交给哪个角色处理。\n"
        "可选角色：pm(产品经理) / arch(架构师) / backend(后端) / frontend(前端) / reviewer(代码评审) / qa(测试)\n"
        "规则：\n"
        "- 前端问题（页面/交互/样式/API 调用）→ frontend\n"
        "- 后端问题（接口/数据/业务逻辑/服务）→ backend\n"
        "- 架构/技术选型/接口契约 → arch\n"
        "- 需求/PRD → pm\n"
        "- 代码评审 → reviewer\n"
        "- 测试 → qa\n"
        "- 如果 Leader 是在向某个角色提问/讨论（而非描述问题），输出 current\n"
        '只输出 JSON：{"owner": "角色 key 或 current", "reason": "一句话原因"}\n\n'
        f"Leader 指令：{instruction}\n\n"
        f"近期群聊：{(history_text or '（暂无）')[:1500]}"
    )
    try:
        r = await router.create([UserMessage(content=prompt, source="user")])
        data = _extract_json(getattr(r, "content", "") or "") or {}
        owner = str(data.get("owner", "current")).strip().lower()
        if owner not in ("pm", "arch", "backend", "frontend", "reviewer", "qa", "current"):
            owner = "current"
        return {"owner": owner, "reason": str(data.get("reason", ""))}
    except Exception:
        return {"owner": "current", "reason": "路由失败，保持原角色"}


async def run_direct(role_key: str, instruction: str, history_text: str = "",
                     on_event=None, usage=None):
    """单角色私聊：先经路由器判断归属，归属其他角色则自动转交（单次，防循环）。"""
    on_event = on_event or (lambda *a, **k: None)
    usage = usage or UsageTracker()
    store = UsageStore(current_usage_path())
    memory = ProjectMemory(current_memory_path())
    agents = build_agents(dev_tools=[code_tool, write_file_tool],
                          inspect_tools=[list_files_tool, read_file_tool],
                          tracker=usage)

    # 路由（单次，不递归 -> 天然防循环）
    target = role_key
    routed = await _route_instruction(instruction, history_text, usage)
    owner = routed.get("owner", "current")
    if owner not in ("current", role_key):
        target = owner
        on_event("stage", "", f"路由：{instruction[:40]} → {DISPLAY_NAMES.get(owner, owner)}（原定 {DISPLAY_NAMES.get(role_key, role_key)}）")
        reason = routed.get("reason") or ""
        if reason:
            on_event("stage", "", f"原因：{reason}")

    agent = agents[target]
    base = memory.context_for(target)
    task = (f"{base}\n\n近期群聊：\n{history_text or '（暂无）'}\n\n"
            f"Leader 单独给你下达指令：{instruction}\n\n"
            "请直接回应并执行该指令。")
    try:
        await _consume(build_stage(agent).run_stream(task=task), on_event)
        on_event("usage", "", json.dumps(usage.snapshot(), ensure_ascii=False))
    finally:
        store.append_run(usage.snapshot(), mode=f"direct:{target}", label=instruction)


async def run_swarm(instruction: str, on_event=None, usage=None):
    """群聊协作模式：Swarm handoff 处理问题（TechLead 调度 + 角色互转）。"""
    on_event = on_event or (lambda *a, **k: None)
    usage = usage or UsageTracker()
    store = UsageStore(current_usage_path())
    memory = ProjectMemory(current_memory_path())
    agents = build_agents(dev_tools=[code_tool, write_file_tool],
                          inspect_tools=[list_files_tool, read_file_tool],
                          tracker=usage, enable_handoffs=True)
    from src.swarm_team import build_swarm_team
    team = build_swarm_team(agents)
    ws = _init_workspace()
    task = (f"{instruction}\n\n"
            f"工作目录：{ws}（后端写 backend/ 子目录，前端写 frontend/ 子目录）")
    try:
        await _consume(team.run_stream(task=task), on_event)
        on_event("usage", "", json.dumps(usage.snapshot(), ensure_ascii=False))
    finally:
        store.append_run(usage.snapshot(), mode="swarm", label=instruction)


async def _classify_task(requirement: str, usage) -> dict:
    """判断任务类型：pipeline（新项目/大需求） or swarm（问题/迭代/讨论）。"""
    router = make_client(tracker=usage)
    prompt = (
        "判断下面用户任务属于哪一类：\n"
        "A) 新项目 / 大需求 / 从零开始开发 → 需要标准流程（PRD→架构→审批→开发→评审→QA）\n"
        "B) 问题修复 / 小改动 / 咨询讨论 → 协作流转即可\n"
        '只输出 JSON：{"mode": "pipeline"|"swarm", "reason": "一句话原因"}\n\n'
        f"用户任务：{requirement}"
    )
    try:
        r = await router.create([UserMessage(content=prompt, source="user")])
        data = _extract_json(getattr(r, "content", "") or "") or {}
        mode = str(data.get("mode", "pipeline")).strip().lower()
        if mode not in ("pipeline", "swarm"):
            mode = "pipeline"
        return {"mode": mode, "reason": str(data.get("reason", ""))}
    except Exception:
        return {"mode": "pipeline", "reason": "路由失败，默认标准流程"}


async def run_auto(requirement: str, on_event=None, ask=None,
                   stop_event=None, usage=None):
    """统一入口：先判断任务类型，再走固定流程或群聊协作。"""
    on_event = on_event or (lambda *a, **k: None)
    usage = usage or UsageTracker()

    routed = await _classify_task(requirement, usage)
    mode = routed.get("mode", "pipeline")
    reason = routed.get("reason") or ""
    label = "标准开发流程" if mode == "pipeline" else "群聊协作"
    on_event("stage", "", f"任务类型判断：{label}" + (f"（{reason}）" if reason else ""))

    if mode == "swarm":
        await run_swarm(requirement, on_event, usage)
    else:
        await run_pipeline(requirement, on_event, ask, stop_event=stop_event, usage=usage)

