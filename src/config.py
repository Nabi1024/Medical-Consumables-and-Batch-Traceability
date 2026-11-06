"""配置管理模块。

集中管理 LLM 网关、数据库路径、JWT 密钥、嵌入 provider 等运行配置，
支持通过环境变量覆盖默认值，便于在不同部署环境（院内离线 / 云端）之间切换。

设计要点：
- 零依赖：仅使用标准库，保证离线环境也能正常加载；
- 默认值偏「离线安全」：未配置 LLM_API_KEY 时自动走 Mock，系统不报错；
- 生产环境务必通过环境变量覆盖 JWT_SECRET、LLM_API_KEY 等敏感项。
"""

import os
from typing import Dict


# 默认配置（离线安全）
_DEFAULTS: Dict[str, str] = {
    "LLM_API_KEY": "",                              # 真实大模型密钥；留空 => 离线 Mock
    "LLM_BASE_URL": "https://api.deepseek.com/v1",  # 兼容 OpenAI 协议的网关地址
    "LLM_MODEL": "deepseek-chat",
    "LLM_PROVIDER": "auto",                         # auto / openai / deepseek / mock
    "EMBED_PROVIDER": "offline",                    # offline / sklearn / api
    "DB_PATH": "agent.db",                          # SQLite 持久化文件路径
    "JWT_SECRET": "change-me-in-production",         # 生产环境务必通过环境变量覆盖
    "JWT_ALGO": "HS256",
    "TOP_K": "3",                                   # 检索返回的片段数量
    "MAX_AGENT_ITERS": "5",                         # Agent 编排单轮最大迭代次数
    "HOST": "0.0.0.0",
    "PORT": "8000",
}


def load_config(env: Dict[str, str] = None) -> Dict[str, str]:
    """从环境变量加载配置，未设置的项回退到默认值。

    Args:
        env: 可选，自定义环境变量字典（主要用于离线测试时注入）。

    Returns:
        合并后的完整配置字典。
    """
    env = env if env is not None else dict(os.environ)
    cfg = dict(_DEFAULTS)
    for key in _DEFAULTS:
        if key in env and env[key] != "":
            cfg[key] = env[key]
    return cfg


def is_offline(cfg: Dict[str, str]) -> bool:
    """判断当前是否处于离线（无真实大模型）模式。"""
    return not bool(cfg.get("LLM_API_KEY"))


def as_int(cfg: Dict[str, str], key: str, default: int = 0) -> int:
    """把配置中的字符串安全转换为整数。"""
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


if __name__ == "__main__":
    # 直接运行可查看当前生效配置（隐藏密钥明文）
    c = load_config()
    print("===== 当前生效配置 =====")
    for k, v in c.items():
        if "SECRET" in k or "KEY" in k:
            print(f"  {k} = {'<已设置>' if v else '<未设置/离线>'}")
        else:
            print(f"  {k} = {v}")
    print(f"离线模式: {is_offline(c)}")
