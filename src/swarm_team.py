"""群聊协作团队：Swarm handoff 模式。

- TechLead（架构师兼任）调度：判断归属 → handoff
- 各角色按需响应，可互相 handoff
- 终止：@用户 / 已解决 / 防循环 / 最大轮次
"""
from autogen_agentchat.teams import Swarm
from autogen_agentchat.conditions import MaxMessageTermination, TextMentionTermination
from autogen_agentchat.base import TerminationCondition
from autogen_agentchat.messages import (StopMessage, HandoffMessage, TextMessage,
                                        ToolCallRequestEvent)


class LoopGuard(TerminationCondition):
    """防循环：同一对 handoff 出现 >= max_per_pair 次，或总 handoff 数 >= max_total，终止。"""

    def __init__(self, max_per_pair: int = 2, max_total: int = 12, max_repeat: int = 3,
                 max_tools_per_agent: int = 20):
        self._max_per_pair = max_per_pair
        self._max_total = max_total
        self._max_repeat = max_repeat
        self._max_tools_per_agent = max_tools_per_agent
        self._terminated = False

    @property
    def terminated(self) -> bool:
        return self._terminated

    async def __call__(self, messages):
        pairs = {}
        total = 0
        for m in messages:
            if isinstance(m, HandoffMessage):
                total += 1
                key = (getattr(m, "source", ""), getattr(m, "target", ""))
                pairs[key] = pairs.get(key, 0) + 1
        for key, cnt in pairs.items():
            if cnt >= self._max_per_pair:
                self._terminated = True
                return StopMessage(
                    content=f"检测到 {key[0]}→{key[1]} 重复 handoff {cnt} 次，终止",
                    source="LoopGuard")
        if total >= self._max_total:
            self._terminated = True
            return StopMessage(content=f"handoff 总数 {total} 超限，终止", source="LoopGuard")
        # 工具调用总数检测：防止单 agent 过度排查
        tool_counts = {}
        for m in messages:
            if isinstance(m, ToolCallRequestEvent):
                src = getattr(m, "source", "")
                tool_counts[src] = tool_counts.get(src, 0) + 1
        for src, cnt in tool_counts.items():
            if cnt >= self._max_tools_per_agent:
                self._terminated = True
                return StopMessage(
                    content=f"检测到 {src} 工具调用 {cnt} 次超限，终止",
                    source="LoopGuard")
        # 重复文本检测：同一 agent 连续输出相同/相似文本
        text_counts = {}
        for m in messages:
            if isinstance(m, TextMessage):
                src = getattr(m, "source", "")
                key = (src, (m.content or "")[:80])
                text_counts[key] = text_counts.get(key, 0) + 1
                if text_counts[key] >= self._max_repeat:
                    self._terminated = True
                    return StopMessage(
                        content=f"检测到 {src} 重复输出相同内容 {text_counts[key]} 次，终止",
                        source="LoopGuard")
        return None

    async def reset(self) -> None:
        self._terminated = False


def build_swarm_team(agents):
    """构建群聊协作团队（TechLead 先发言调度，其余角色可互相 handoff）。"""
    termination = (
        TextMentionTermination("已解决")
        | TextMentionTermination("@用户")
        | TextMentionTermination("需要用户确认")
        | LoopGuard(max_per_pair=2, max_total=12, max_repeat=3, max_tools_per_agent=20)
        | MaxMessageTermination(24)
    )
    return Swarm(
        [
            agents["arch"],      # TechLead：调度入口
            agents["backend"],
            agents["frontend"],
            agents["reviewer"],
            agents["qa"],
            agents["pm"],
        ],
        termination_condition=termination,
    )
