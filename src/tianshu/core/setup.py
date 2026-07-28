"""首次启动配置向导 — 交互式 API Key 设置。

商用场景：新用户第一次运行 tianshu 时，自动引导配置 API Key。
配置存储在 ~/.tianshu/config.yaml，不依赖环境变量。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

# 用户配置目录
USER_CONFIG_DIR = Path.home() / ".tianshu"
USER_CONFIG_FILE = USER_CONFIG_DIR / "config.yaml"


# ── 内置 Provider 模板 ────────────────────────────────────────────────────
# 当 config/providers.yaml 不可用时，使用此内置模板。

BUILTIN_PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek（深度求索）",
        "description": "V4 Pro(深度推理·1M上下文) + V4 Flash(快速响应·1M上下文)，¥1/百万token",
        "signup_url": "https://platform.deepseek.com/api_keys",
        "models": ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"],
        "env_var": "DEEPSEEK_API_KEY",
    },
    "doubao": {
        "name": "豆包（字节跳动火山引擎）",
        "description": "128K超长上下文，¥0.0008/千token，适合轻量任务",
        "signup_url": "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
        "models": ["doubao-lite-128k", "doubao-pro-32k"],
        "env_var": "DOUBAO_API_KEY",
    },
    "kimi": {
        "name": "Kimi（月之暗面 Moonshot）",
        "description": "128K上下文，长文档处理能力强",
        "signup_url": "https://platform.moonshot.cn/console/api-keys",
        "models": ["moonshot-v1-128k", "moonshot-v1-8k"],
        "env_var": "MOONSHOT_API_KEY",
    },
    "glm": {
        "name": "GLM（智谱AI）",
        "description": "清华系，多模态+代码能力强，GLM-4系列",
        "signup_url": "https://open.bigmodel.cn/usercenter/apikeys",
        "models": ["glm-4-plus", "glm-4-flash"],
        "env_var": "ZHIPU_API_KEY",
    },
}


# ── 读取已保存的 Key ─────────────────────────────────────────────────────

def load_user_keys() -> dict[str, str]:
    """从 ~/.tianshu/config.yaml 读取用户保存的 API Key。

    Returns:
        {provider_name: api_key} 字典，文件不存在则返回空字典。
    """
    if not USER_CONFIG_FILE.exists():
        return {}
    try:
        with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        keys: dict[str, str] = {}
        for name, cfg in data.items():
            if isinstance(cfg, dict) and cfg.get("api_key"):
                keys[name] = cfg["api_key"]
            elif isinstance(cfg, str):
                keys[name] = cfg
        return keys
    except Exception:
        return {}


def save_user_keys(keys: dict[str, str]) -> None:
    """保存 API Key 到 ~/.tianshu/config.yaml。

    Args:
        keys: {provider_name: api_key}
    """
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {name: {"api_key": key} for name, key in keys.items() if key}
    with open(USER_CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


# ── 检查 Key 状态 ────────────────────────────────────────────────────────

def check_keys() -> dict[str, bool]:
    """检查所有 provider 的 Key 是否已配置。

    Returns:
        {provider_name: has_key} 字典。
    """
    user_keys = load_user_keys()
    result: dict[str, bool] = {}
    for name, info in BUILTIN_PROVIDERS.items():
        env_key = os.environ.get(info["env_var"], "")
        file_key = user_keys.get(name, "")
        result[name] = bool(env_key or file_key)
    return result


def any_key_configured() -> bool:
    """是否有至少一个 provider 配置了 Key。"""
    return any(check_keys().values())


# ── 默认模型偏好 ──────────────────────────────────────────────────────────

def load_default_model() -> str:
    """从 ~/.tianshu/config.yaml 读取用户设置的默认模型。

    Returns:
        "deepseek/v4-pro" 或 ""（未设置）
    """
    if not USER_CONFIG_FILE.exists():
        return ""
    try:
        with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("default_model", "")
    except Exception:
        return ""


def save_default_model(model_ref: str) -> None:
    """保存默认模型到 ~/.tianshu/config.yaml。

    保留已有的 API Key 配置，只更新 default_model 字段。
    """
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if USER_CONFIG_FILE.exists():
        try:
            with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            pass
    data["default_model"] = model_ref
    with open(USER_CONFIG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


# ── 交互式配置向导 ────────────────────────────────────────────────────────

def run_setup_wizard() -> dict[str, str]:
    """交互式 API Key 配置向导。

    打印每个 provider 的介绍和注册链接，引导用户输入 Key。
    输入空值跳过，输入 'q' 退出。

    Returns:
        用户配置的 {provider_name: api_key} 字典。
    """
    print()
    print("╔" + "═" * 56 + "╗")
    print("║  天枢 Agent · 首次配置向导                              ║")
    print("║  请至少配置一个模型提供商的 API Key                        ║")
    print("╚" + "═" * 56 + "╝")
    print()
    print("  提示: 输入 Key 后回车保存，留空回车跳过，输入 q 退出")
    print()

    existing = load_user_keys()
    new_keys: dict[str, str] = {}

    for i, (provider_id, info) in enumerate(BUILTIN_PROVIDERS.items(), 1):
        existing_key = existing.get(provider_id, "")
        masked = _mask_key(existing_key) if existing_key else ""

        print(f"  ── {i}. {info['name']} ──")
        print(f"     {info['description']}")
        print(f"     获取 Key: {info['signup_url']}")
        if masked:
            print(f"     已保存: {masked}（回车保持不变）")

        try:
            user_input = input(f"     {provider_id} API Key: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n  配置已保存。")
            break

        if user_input.lower() == "q":
            print("\n  已退出配置。")
            break
        elif user_input:
            new_keys[provider_id] = user_input
        elif existing_key:
            new_keys[provider_id] = existing_key  # 保持旧值
        print()

    # 合并
    all_keys = {**existing, **new_keys}
    return all_keys


def _mask_key(key: str) -> str:
    """脱敏显示 Key。sk-abc123xyz789 → sk-abc1***789"""
    if len(key) <= 8:
        return key[:4] + "****"
    return key[:7] + "****" + key[-4:]


# ── 快捷入口 ──────────────────────────────────────────────────────────────

def ensure_keys() -> bool:
    """确保至少有一个 Key 可用。没有则启动配置向导。

    Returns:
        True 如果配置成功（有可用 key），False 如果用户跳过。
    """
    if any_key_configured():
        return True

    print("\n  ⚠️  未检测到任何 API Key。进入首次配置...")
    keys = run_setup_wizard()
    if keys:
        save_user_keys(keys)
        return True
    return False
