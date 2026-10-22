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


def load_prompts() -> dict:
    with open(PROMPT_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_agents(dev_tools=None, inspect_tools=None, tracker=None):
    """返回以角色 key 为键的 Agent 字典。

    dev_tools：给后端/前端（写代码、跑命令）
    inspect_tools：给评审/QA（只读查看文件）
    QA 同时拥有两类工具（跑测试 + 读文件）。
    """
    p = load_prompts()
    dev_tools = dev_tools or []
    inspect_tools = inspect_tools or []

    def cli(role):
        return make_client(role=role, tracker=tracker)

    return {
        "pm": AssistantAgent("ProductManager", model_client=cli("pm"), system_message=p["pm"]),
        "arch": AssistantAgent("Architect", model_client=cli("arch"), system_message=p["arch"]),
        "backend": AssistantAgent("BackendDev", model_client=cli("backend"), system_message=p["be"], tools=dev_tools),
        "frontend": AssistantAgent("FrontendDev", model_client=cli("frontend"), system_message=p["fe"], tools=dev_tools),
        "reviewer": AssistantAgent("Reviewer", model_client=cli("reviewer"), system_message=p["review"], tools=inspect_tools),
        "qa": AssistantAgent("QA", model_client=cli("qa"), system_message=p["qa"], tools=dev_tools + inspect_tools),
        "leader": UserProxyAgent("Leader", description="人类Leader，只负责审批架构与拍板"),
    }
