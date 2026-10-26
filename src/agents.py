"""构建 6 个角色 Agent + 1 个 Leader（人类），支持按角色混合模型。

注意：Agent 的 name 必须是 ASCII（OpenAI 兼容接口的 message.name 只允许
字母/数字/_/-），中文角色名放在 system_message 与 DISPLAY_NAMES 里。
"""
from pathlib import Path
import yaml
from autogen_agentchat.agents import AssistantAgent, UserProxyAgent
from .models import make_client

from .paths import CONFIG_DIR
PROMPT_FILE = CONFIG_DIR / "agents.yaml"

# ASCII 标识 -> 中文显示名（UI 层渲染用）
DISPLAY_NAMES = {
    "pm": "产品经理",
    "arch": "架构师",
    "backend": "后端开发",
    "frontend": "前端开发",
    "reviewer": "代码评审",
    "qa": "QA测试",
    "leader": "Leader",
}

# 群聊协作模式：每个角色可以 handoff 给哪些 agent（用 agent name）
HANDOFF_TARGETS = {
    "pm": ["Architect"],
    "arch": ["BackendDev", "FrontendDev", "Reviewer", "QA", "ProductManager"],
    "backend": ["Architect", "FrontendDev", "Reviewer", "QA"],
    "frontend": ["Architect", "BackendDev", "Reviewer", "QA"],
    "reviewer": ["Architect", "BackendDev", "FrontendDev", "QA"],
    "qa": ["Architect", "BackendDev", "FrontendDev"],
}

def load_prompts() -> dict:
    with open(PROMPT_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_agents(dev_tools=None, inspect_tools=None, tracker=None,
                 enable_handoffs: bool = False):
    """返回以角色 key 为键的 Agent 字典。

    dev_tools：给后端/前端（写代码、跑命令）
    inspect_tools：给评审/QA（只读查看文件）
    QA 同时拥有两类工具（跑测试 + 读文件）。
    enable_handoffs=True 时启用群聊协作（拼 handoff 提示词 + handoffs 目标）。
    """
    p = load_prompts()
    dev_tools = dev_tools or []
    inspect_tools = inspect_tools or []

    def cli(role):
        return make_client(role=role, tracker=tracker)

    def make(role_key, name, prompt_key, tools=None):
        prompt = p[prompt_key] or ""
        kwargs = {"model_client": cli(role_key), "system_message": prompt}
        if tools:
            kwargs["tools"] = tools
        if enable_handoffs:
            swarm_prompt = (p.get(role_key + "_swarm") or "").strip()
            kwargs["system_message"] = (prompt + "\n\n" + swarm_prompt).strip()
            kwargs["handoffs"] = HANDOFF_TARGETS.get(role_key, [])
            kwargs["max_tool_iterations"] = 8  # 群聊模式限制单 turn 工具预算，防过度排查
        return AssistantAgent(name, **kwargs)

    return {
        "pm": make("pm", "ProductManager", "pm"),
        "arch": make("arch", "Architect", "arch"),
        "backend": make("backend", "BackendDev", "be", dev_tools),
        "frontend": make("frontend", "FrontendDev", "fe", dev_tools),
        "reviewer": make("reviewer", "Reviewer", "review", inspect_tools),
        "qa": make("qa", "QA", "qa", dev_tools + inspect_tools),
        "leader": UserProxyAgent("Leader", description="人类Leader，只负责审批架构与拍板"),
    }
