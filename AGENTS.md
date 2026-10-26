# AGENTS.md — 工程规约

> 本项目是「6 角色 AI 软件团队」流水线（Python + AutoGen + PySide6 + RAG）。
> 本文件是**可判定的硬规则**，不是口号。违反任何一条都会导致实际 bug。

## 硬约束（违反必出 bug）

1. **Agent name 必须 ASCII**：OpenAI 兼容接口的 `message.name` 只允许字母/数字/_/-。中文名放 `DISPLAY_NAMES`，不要进 Agent 的 name。
2. **非 OpenAI 模型必须传 `model_info`**，否则报 `model_info is required`。
3. **终止标记不进任务/交接文本**：`ARCH_DONE="交付完成"`、`QA_DONE="【全部完成】"` 只能由架构师/QA 在自身提示词里输出。任务文本或交接文本里出现字面量会立即触发终止（已踩过两次）。
4. **白名单按整条命令拆段**：`run_cmd` 先按 `&&/||/;/|` 拆段、逐段取首词查白名单；白名单**不得**加入 `rm/del/rmdir` 等删除命令。
5. **项目类型用结构化声明**：架构师输出 `{"project_type": "backend|frontend|fullstack"}` 或中文「项目类型：xxx」。`_detect_project_type` 只把关键词当低置信度启发式兜底。
6. **并行开发写隔离子目录**：前后端并行时，后端写 `workspace/backend/`、前端写 `workspace/frontend/`，不得共写根目录（历史 bug：`run_backend()` 漏传 `parallel=True`）。

## 约定（一致性）

- 记忆字段固定 8 个：`requirement / prd / leader_answers / architecture / backend / frontend / review / qa`。
- 长字段（>3000 字）走 RAG 检索（Milvus + embedding），短字段直接保留；检索失败降级截断，不得崩。
- **先落盘再发事件**：`memory.save()` / `store.append_run()` 要在对应 `done`/usage 事件发出之前，崩溃可恢复。
- DeepSeek 的 Anthropic 端点是 `/anthropic` 不是 `/v1`。

## 角色私聊自动路由（2026-10-26）

- `run_direct` 先经 `_route_instruction` 判断归属（pm/arch/backend/frontend/reviewer/qa/current）。
- 归属其他角色则**单次转交**（不递归，天然防循环），并发出 `stage` 路由事件。
- 解析失败 / 问询 / 讨论 → owner=current（保持原角色）。
- 路由判断异常时保持原角色（安全降级，不阻断私聊）。

## 群聊协作（Swarm）

- 用 AutoGen **Swarm（handoff）** 实现，不要用自由群聊循环（Selector 会空转）。
- 每个角色必须 handoff 或输出终止词（`已解决` / `@用户` / `需要用户确认`），否则流程卡死。
- 三重防循环：`LoopGuard`（handoff 循环 / 重复文本 / 工具超限）+ `max_tool_iterations=8`。
- 群聊提示词放在 `config/agents.yaml` 的 `*_swarm` 段落，不要硬编码在代码里。
- **DeepSeek 多轮必须关闭 thinking**（`disable_thinking`），否则 reasoning_content 回传报 400。

## 禁止

- 禁止把删除类命令加入白名单。
- 禁止在任务文本里写「交付完成」「【全部完成】」字面量。
- 禁止自由群聊循环（Selector 会空转）；固定串行 SOP，只有前后端可并行。
- 禁止在 `core` 层引入 UI 依赖、在非 UI 层读 `process.env`（分层边界）。

## 关键文件

- `src/pipeline.py`：流水线、项目类型判断、前后端并行
- `src/agents.py`：6 角色 + DISPLAY_NAMES
- `src/memory.py`：项目记忆 + RAG 检索式上下文
- `src/rag.py`：切片 + embedding + Milvus 向量检索
- `src/tools/code_exec.py`：白名单命令 + 文件工具 + git 快照
