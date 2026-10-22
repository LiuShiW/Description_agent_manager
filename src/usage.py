"""Token 用量与成本统计（按角色、按模型），支持预算上限。"""
import threading
import yaml

from .paths import CONFIG_DIR

# 人民币 / 百万 token（官方价格，可按需在 config 的 pricing 中覆盖）
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
        p = self.pricing.get(model, {"input": 3.0, "output": 6.0})
        cost = (prompt_tokens or 0) / 1e6 * p["input"] + (completion_tokens or 0) / 1e6 * p["output"]
        with self._lock:
            d = self._data.setdefault(role, {
                "role": role, "model": model, "input": 0, "output": 0,
                "calls": 0, "cost": 0.0,
            })
            d["model"] = model
            d["input"] += int(prompt_tokens or 0)
            d["output"] += int(completion_tokens or 0)
            d["calls"] += 1
            d["cost"] += cost

    def snapshot(self) -> dict:
        with self._lock:
            roles = sorted(self._data.values(), key=lambda x: -x["cost"])
            return {
                "roles": [{**r, "cost": round(r["cost"], 6)} for r in roles],
                "total_input": sum(r["input"] for r in roles),
                "total_output": sum(r["output"] for r in roles),
                "total_cost": round(sum(r["cost"] for r in roles), 6),
                "calls": sum(r["calls"] for r in roles),
                "budget_cny": self.budget_cny,
            }

    def exceeded(self) -> bool:
        return self.budget_cny > 0 and self.snapshot()["total_cost"] >= self.budget_cny


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
