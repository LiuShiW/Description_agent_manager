"""M1 冒烟测试：最简 2-agent 群聊（1+1=?），验证环境与模型连通。"""
import asyncio
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.teams import RoundRobinGroupChat
from autogen_agentchat.conditions import MaxMessageTermination
from autogen_agentchat.ui import Console

from src.models import make_client


async def main():
    llm = make_client()
    asker = AssistantAgent(
        "Asker",
        model_client=llm,
        system_message="你是提问者。你的任务只有一件：向对方提问「1+1 等于几？」，然后停止。",
    )
    answerer = AssistantAgent(
        "Answerer",
        model_client=llm,
        system_message="你是回答者。当被问到「1+1 等于几」时，回答「2」。",
    )
    team = RoundRobinGroupChat(
        [asker, answerer],
        termination_condition=MaxMessageTermination(4),
    )
    await Console(team.run_stream(task="开始：提问者先向回答者提问 1+1 等于几。"))


if __name__ == "__main__":
    asyncio.run(main())
