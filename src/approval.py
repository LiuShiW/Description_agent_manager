"""两段式人工审批：阶段一产出 PRD+架构 -> 人审 -> 阶段二开发测试。"""
from dataclasses import dataclass


@dataclass
class ApprovalResult:
    approved: bool
    note: str = ""


def ask_leader_cli(prompt: str) -> ApprovalResult:
    ans = input(f"\n[Leader 审批] {prompt} (y/n): ").strip().lower()
    return ApprovalResult(approved=ans in ("y", "yes", "通过", "1"))
