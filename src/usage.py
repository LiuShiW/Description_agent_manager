"""Token 用量与成本统计（按实际模型计价），以及按项目持久化。

存储结构（项目目录/usage.json）：
{
  "total": {"total_input":..., "total_output":..., "total_cost":..., "calls":..., "cost_known": true},
  "runs": [
    {"id":..., "time":..., "mode":"pipeline", "label":"...",
     "total_input":..., "total_output":..., "total_cost":..., "calls":...,
     "cost_known":true, "unknown_models":[], "roles":[...]}
  ]
}
"""
import datetime
import json
import threading
import uuid

import yaml

from .paths import CONFIG_DIR

# 人民币 / 百万 token。只内置已核实的 DeepSeek 价格；
# 其他模型请在 config/models.yaml 的 pricing 中按官方价格填写。
DEFAULT_PRICING = {
    "deepseek-flash": {"input": 1.0, "output": 2.0},
    "deepseek-v4-pro": {"input": 3.0, "output": 6.0},
    "deepseek-v4-pro-0813": {"input": 3.0, "output": 6.0},
}


def _load_config() -> dict:
    try:
        return yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def load_pricing() -> dict:
    pricing = dict(DEFAULT_PRICING)
    custom = _load_config().get("pricing") or {}
    for model, p in custom.items():
        if isinstance(p, dict):
            pricing[model] = {
                "input": float(p.get("input", 0) or 0),
                "output": float(p.get("output", 0) or 0),
            }
    return pricing


def load_budget() -> float:
    try:
        return float(_load_config().get("budget_cny", 0) or 0)
    except Exception:
        return 0.0


class UsageTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = {}
        self.pricing = load_pricing()
        self.budget_cny = load_budget()

    def add(self, role: str, model: str, prompt_tokens: int, completion_tokens: int):
        role = role or "unknown"
        model = model or "unknown"
        p = self.pricing.get(model)
        known = p is not None
        cost = 0.0
        if known:
            cost = ((prompt_tokens or 0) / 1e6 * p["input"]
                    + (completion_tokens or 0) / 1e6 * p["output"])
        with self._lock:
            d = self._data.setdefault(role, {
                "role": role, "model": model, "input": 0, "output": 0,
                "calls": 0, "cost": 0.0, "known": True,
            })
            d["model"] = model
            d["input"] += int(prompt_tokens or 0)
            d["output"] += int(completion_tokens or 0)
            d["calls"] += 1
            d["cost"] += cost
            if not known:
                d["known"] = False

    def snapshot(self) -> dict:
        with self._lock:
            roles = sorted(self._data.values(), key=lambda x: -x["cost"])
            unknown = sorted({r["model"] for r in roles if not r.get("known", True)})
            return {
                "roles": [{**r, "cost": round(r["cost"], 6)} for r in roles],
                "total_input": sum(r["input"] for r in roles),
                "total_output": sum(r["output"] for r in roles),
                "total_cost": round(sum(r["cost"] for r in roles), 6),
                "calls": sum(r["calls"] for r in roles),
                "cost_known": not unknown,
                "unknown_models": unknown,
                "budget_cny": self.budget_cny,
            }

    def exceeded(self) -> bool:
        return self.budget_cny > 0 and self.snapshot()["total_cost"] >= self.budget_cny


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
            "total_cost": snapshot.get("total_cost", 0.0),
            "calls": snapshot.get("calls", 0),
            "cost_known": snapshot.get("cost_known", True),
            "unknown_models": snapshot.get("unknown_models", []),
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
        return {
            "total_input": sum(r.get("total_input", 0) for r in runs),
            "total_output": sum(r.get("total_output", 0) for r in runs),
            "total_cost": round(sum(r.get("total_cost", 0.0) for r in runs), 6),
            "calls": sum(r.get("calls", 0) for r in runs),
            "cost_known": all(r.get("cost_known", True) for r in runs),
        }


def instrument_client(client, tracker: UsageTracker, role: str, model: str):
    """包装 create / create_stream，把每次调用的 usage 记入 tracker。"""
    orig_create = client.create

    async def create(*args, **kwargs):
        result = await orig_create(*args, **kwargs)
        u = getattr(result, "usage", None)
        if u is not None:
            tracker.add(role, model, u.prompt_tokens, u.completion_tokens)
        return result

    client.create = create

    orig_stream = client.create_stream

    def create_stream(*args, **kwargs):
        async def gen():
            async for item in orig_stream(*args, **kwargs):
                u = getattr(item, "usage", None)
                if u is not None:
                    tracker.add(role, model, u.prompt_tokens, u.completion_tokens)
                yield item
        return gen()

    client.create_stream = create_stream
    return client
