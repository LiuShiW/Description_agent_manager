"""模型客户端工厂：统一配置 + 按角色覆盖（model / api_key / base_url）。

配置结构（config/models.yaml）：
default:                 # 统一配置，所有角色默认使用
  model: deepseek-flash
  api_key: sk-...
  base_url: https://api.deepseek.com/v1
  temperature: 0.2

roles:                   # 可选：按角色覆盖；未填字段回落到 default
  arch:
    model: deepseek-v4-pro
  backend:
    model: deepseek-v4-pro
    api_key: sk-...        # 可选：该角色单独使用另一把 Key
    base_url: https://api.deepseek.com/v1
"""
import shutil
from pathlib import Path
import yaml
from autogen_ext.models.openai import OpenAIChatCompletionClient

from .paths import CONFIG_DIR

CONFIG = CONFIG_DIR / "models.yaml"
ROLES = ("pm", "arch", "backend", "frontend", "reviewer", "qa")

# 非 OpenAI 官方模型必须显式声明能力
DEFAULT_MODEL_INFO = {
    "vision": False,
    "function_calling": True,
    "json_output": True,
    "family": "deepseek",
    "structured_output": False,
}


def ensure_config():
    """首次运行时从示例配置初始化 models.yaml。"""
    if not CONFIG.exists():
        example = CONFIG_DIR / "models.example.yaml"
        if example.exists():
            shutil.copyfile(example, CONFIG)


def load_model_cfg() -> dict:
    ensure_config()
    with open(CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_model_cfg(data: dict):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                      encoding="utf-8")


def role_override(data: dict, role: str) -> dict:
    """读取角色覆盖；兼容旧的 role_models: {role: model} 写法。"""
    ov = (data.get("roles") or {}).get(role)
    if isinstance(ov, str):
        ov = {"model": ov}
    if not ov:
        legacy = (data.get("role_models") or {}).get(role)
        if isinstance(legacy, str):
            ov = {"model": legacy}
    return dict(ov or {})


def resolve_role_cfg(role: str | None = None) -> dict:
    data = load_model_cfg()
    cfg = dict(data.get("default") or {})
    if role:
        for k, v in role_override(data, role).items():
            if v not in (None, ""):
                cfg[k] = v
    return cfg


def _checked(cfg: dict) -> dict:
    key = str(cfg.get("api_key", ""))
    if not key or "your-" in key or "填入" in key:
        raise RuntimeError("请先在「设置」中填入 API Key（config/models.yaml）")
    return cfg


def make_client(model: str | None = None, temperature: float | None = None,
                role: str | None = None, tracker=None):
    """role 不为空时使用该角色的覆盖配置；否则使用统一配置。"""
    cfg = _checked(resolve_role_cfg(role))
    provider = str(cfg.get("provider", "deepseek")).lower()
    info = dict(DEFAULT_MODEL_INFO)
    info["family"] = "deepseek" if provider == "deepseek" else "unknown"
    resolved_model = model or cfg["model"]
    client = OpenAIChatCompletionClient(
        model=resolved_model,
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        temperature=temperature if temperature is not None else cfg.get("temperature", 0.2),
        model_info=info,
    )
    if tracker is not None:
        from .usage import instrument_client
        instrument_client(client, tracker, role or "default", resolved_model,
                          disable_thinking=(provider == "deepseek"))
    return client


def load_role_models() -> dict:
    """向后兼容：返回 role -> model 的映射。"""
    data = load_model_cfg()
    out = {}
    for role in ROLES:
        m = role_override(data, role).get("model")
        if m:
            out[role] = m
    return out
