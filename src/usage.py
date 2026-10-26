"""Token 用量统计与按项目持久化（不计算金额，不设预算上限）。

只记录 input/output token 与调用次数，避免内置模型价格滞后或误算。

存储结构（项目根/usage.json）：
{
  "total": {"total_input":..., "total_output":..., "total_tokens":..., "calls":...},
  "runs": [
    {"id":..., "time":..., "mode":"pipeline", "label":"...",
     "total_input":..., "total_output":..., "total_tokens":..., "calls":...,
     "roles":[...]}
  ]
}
"""
import datetime
import json
import threading
import uuid


class UsageTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}

    def add(self, role: str, model: str, prompt_tokens: int, completion_tokens: int):
        role = role or "unknown"
        model = model or "unknown"
        with self._lock:
            d = self._data.setdefault(role, {
                "role": role, "model": model, "input": 0, "output": 0, "calls": 0,
            })
            d["model"] = model
            d["input"] += int(prompt_tokens or 0)
            d["output"] += int(completion_tokens or 0)
            d["calls"] += 1

    def snapshot(self) -> dict:
        with self._lock:
            roles = sorted(self._data.values(), key=lambda x: -(x["input"] + x["output"]))
            total_in = sum(r["input"] for r in roles)
            total_out = sum(r["output"] for r in roles)
            return {
                "roles": list(roles),
                "total_input": total_in,
                "total_output": total_out,
                "total_tokens": total_in + total_out,
                "calls": sum(r["calls"] for r in roles),
            }


class UsageStore:
    """按项目持久化用量；每次运行/对话作为一条 run。"""

    def __init__(self, path):
        from pathlib import Path
        self.path = Path(path)

    def load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("total", self._sum([]))
                    data.setdefault("runs", [])
                    return data
            except Exception:
                pass
        return {"total": self._sum([]), "runs": []}

    def append_run(self, snapshot: dict, mode: str = "pipeline", label: str = "") -> dict:
        data = self.load()
        run = {
            "id": uuid.uuid4().hex[:12],
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "label": (label or "")[:120],
            "total_input": snapshot.get("total_input", 0),
            "total_output": snapshot.get("total_output", 0),
            "total_tokens": snapshot.get("total_tokens", 0),
            "calls": snapshot.get("calls", 0),
            "roles": snapshot.get("roles", []),
        }
        data["runs"].append(run)
        data["runs"] = data["runs"][-500:]
        data["total"] = self._sum(data["runs"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    @staticmethod
    def _sum(runs) -> dict:
        total_in = sum(r.get("total_input", 0) for r in runs)
        total_out = sum(r.get("total_output", 0) for r in runs)
        return {
            "total_input": total_in,
            "total_output": total_out,
            "total_tokens": total_in + total_out,
            "calls": sum(r.get("calls", 0) for r in runs),
        }


def instrument_client(client, tracker: UsageTracker, role: str, model: str,
                      disable_thinking: bool = False):
    """包装 create / create_stream，把每次调用的 usage 记入 tracker。

    disable_thinking=True 时给每次调用注入 extra_body={"thinking": {"type": "disabled"}}：
    DeepSeek thinking 模型在多轮对话中要求 reasoning_content 原样回传，
    关闭 thinking 可避免 400（固定流程单轮不受影响，Swarm 多轮必需）。
    """
    orig_create = client.create

    def _inject(kwargs):
        if not disable_thinking:
            return kwargs
        extra = dict(kwargs.get("extra_create_args") or {})
        body = dict(extra.get("extra_body") or {})
        body["thinking"] = {"type": "disabled"}
        extra["extra_body"] = body
        kwargs["extra_create_args"] = extra
        return kwargs

    async def create(*args, **kwargs):
        result = await orig_create(*args, **_inject(kwargs))
        u = getattr(result, "usage", None)
        if u is not None:
            tracker.add(role, model, u.prompt_tokens, u.completion_tokens)
        return result

    client.create = create

    orig_stream = client.create_stream

    def create_stream(*args, **kwargs):
        async def gen():
            async for item in orig_stream(*args, **_inject(kwargs)):
                u = getattr(item, "usage", None)
                if u is not None:
                    tracker.add(role, model, u.prompt_tokens, u.completion_tokens)
                yield item
        return gen()

    client.create_stream = create_stream
    return client
