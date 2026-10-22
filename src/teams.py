"""群聊与单代理阶段的构建与终止条件。"""
from autogen_agentchat.teams import RoundRobinGroupChat, SelectorGroupChat
from autogen_agentchat.conditions import TextMentionTermination, MaxMessageTermination
from autogen_agentchat.base import TerminationCondition
from autogen_agentchat.messages import StopMessage, TextMessage


class StopAfterText(TerminationCondition):
    """产出 n 条来自 Agent 的文本消息后停止（忽略工具调用摘要，避免中途截断 Agent 的多步工具调用）。"""

    def __init__(self, n: int = 1):
        self._n = n
        self._count = 0

    @property
    def terminated(self) -> bool:
        return self._count >= self._n

    async def __call__(self, messages) -> StopMessage | None:
        if self.terminated:
            raise RuntimeError("StopAfterText 已触发")
        for m in messages:
            if isinstance(m, TextMessage) and getattr(m, "source", "") != "user":
                self._count += 1
        if self._count >= self._n:
            return StopMessage(content=f"已产出 {self._count} 条文本消息", source="StopAfterText")
        return None

    async def reset(self) -> None:
        self._count = 0


def build_team(participants, llm, mode="roundrobin", done_text="交付完成", max_messages=300):
    done = TextMentionTermination(done_text) | MaxMessageTermination(max_messages)
    if mode == "selector":
        return SelectorGroupChat(participants, model_client=llm, termination_condition=done)
    return RoundRobinGroupChat(participants, termination_condition=done)


def build_stage(agent):
    """单代理阶段：产出 1 条文本消息即结束（含多步工具调用也不会被中途截断）。"""
    return RoundRobinGroupChat(
        [agent],
        termination_condition=StopAfterText(1) | MaxMessageTermination(50),
    )
