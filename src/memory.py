"""项目记忆：保存各阶段产出，并按角色构建上下文（超长字段走 RAG 检索）。

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
RAG_THRESHOLD = 3000   # 超过此长度的字段改走 RAG 检索，不再截断
RAG_TOP_K = 4          # 每个角色检索保留的相关片段数


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
        """按角色返回所需上下文；超长字段用 RAG 检索替代截断。"""
        titles = {
            "requirement": "原始需求", "prd": "PRD",
            "leader_answers": "Leader 答复", "architecture": "架构设计",
            "backend": "后端交付", "frontend": "前端交付",
            "review": "评审意见", "qa": "QA 报告",
        }
        if role == "arch":
            fields = ["requirement", "prd", "leader_answers"]
        elif role == "backend":
            fields = ["prd", "leader_answers", "architecture"]
        elif role == "frontend":
            fields = ["prd", "architecture"] + ([] if parallel else ["backend"])
        elif role == "reviewer":
            fields = ["architecture", "backend", "frontend"]
        elif role == "qa":
            fields = ["prd", "architecture", "review"]
        else:
            fields = ["requirement", "prd"]

        parts: list[str] = []
        long_docs: dict[str, str] = {}

        for key in fields:
            value = self.get(key)
            if not value.strip():
                continue
            title = titles[key]
            if len(value) <= RAG_THRESHOLD:
                parts.append(f"【{title}】\n{value}")
            else:
                long_docs[title] = value

        if long_docs:
            parts.append(self._retrieve_sections(long_docs, fields))

        return "\n\n".join(parts)

    def _retrieve_sections(self, long_docs: dict[str, str],
                           fields: list[str]) -> str:
        """对超长字段做切片 + 语义检索，返回最相关片段。"""
        try:
            from src.rag import MemoryRetriever
            retriever = MemoryRetriever(persist_dir=str(self.path.parent / ".rag_store"))
            retriever.index(long_docs)
            query = self.get("requirement") or " ".join(fields)
            hits = retriever.retrieve(query, top_k=RAG_TOP_K)
            if hits:
                return "【知识片段（RAG 检索）】\n" + "\n\n".join(hits)
        except Exception:
            pass
        # 检索失败则退回截断，保证不崩
        return "\n\n".join(
            f"【{title}】\n{_clip(value)}" for title, value in long_docs.items()
        )

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
