"""项目记忆：保存各阶段产出，并按角色裁剪上下文。

memory.json（项目根）：
{
  "requirement": "...", "prd": "...", "leader_answers": "...",
  "architecture": "...", "backend": "...", "frontend": "...",
  "review": "...", "qa": "...", "updated": "..."
}
"""
import json
from datetime import datetime
from pathlib import Path

FIELDS = ("requirement", "prd", "leader_answers", "architecture",
          "backend", "frontend", "review", "qa")
MAX_FIELD_CHARS = 12000


def _clip(text: str, limit: int = MAX_FIELD_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（已截断，原长 {len(text)} 字符）"


class ProjectMemory:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self.load()

    def load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated"] = datetime.now().isoformat(timespec="seconds")
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def reset(self, requirement: str):
        self.data = {"requirement": requirement or ""}
        self.save()

    def set(self, key: str, value: str):
        self.data[key] = value or ""
        self.save()

    def get(self, key: str) -> str:
        return self.data.get(key, "") or ""

    def context_for(self, role: str, parallel: bool = False) -> str:
        """按角色返回所需上下文，避免把全量交付物塞给每个模型。"""
        parts = []

        def add(title: str, key: str):
            value = self.get(key)
            if value.strip():
                parts.append(f"【{title}】\n{_clip(value)}")

        if role == "arch":
            add("原始需求", "requirement")
            add("PRD", "prd")
            add("Leader 答复", "leader_answers")
        elif role == "backend":
            add("PRD", "prd")
            add("Leader 答复", "leader_answers")
            add("架构设计", "architecture")
        elif role == "frontend":
            add("PRD", "prd")
            add("架构设计", "architecture")
            if not parallel:
                add("后端交付", "backend")
        elif role == "reviewer":
            add("架构设计", "architecture")
            add("后端交付", "backend")
            add("前端交付", "frontend")
        elif role == "qa":
            add("PRD", "prd")
            add("架构设计", "architecture")
            add("评审意见", "review")
        else:
            add("原始需求", "requirement")
            add("PRD", "prd")

        return "\n\n".join(parts)

    def summary_text(self) -> str:
        titles = {
            "requirement": "原始需求", "prd": "PRD",
            "leader_answers": "Leader 答复", "architecture": "架构设计",
            "backend": "后端交付", "frontend": "前端交付",
            "review": "评审意见", "qa": "QA 报告",
        }
        parts = []
        for key in FIELDS:
            value = self.get(key)
            if value.strip():
                parts.append(f"## {titles[key]}\n{value}")
        return "\n\n".join(parts)
