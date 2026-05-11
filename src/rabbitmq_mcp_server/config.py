"""配置管理模块：加载集群配置和应用设置"""

import os
import re
from pathlib import Path
from typing import Dict

import yaml
from pydantic_settings import BaseSettings


class ClusterConfig(BaseSettings):
    """单个 RabbitMQ 集群的连接配置"""

    url: str  # RabbitMQ Management API 地址，如 http://localhost:15672
    user: str  # 管理用户名
    password: str  # 管理密码，支持 ${ENV_VAR} 语法
    verify_ssl: bool = True  # 是否校验 SSL 证书


class AppConfig(BaseSettings):
    """应用全局配置，优先从环境变量读取 (前缀: RABBITMQ_)"""

    default_cluster: str = "dev"  # 默认使用的集群别名
    api_timeout: int = 15  # Management API 请求超时（秒）
    max_payload: int = 10240  # 消息体最大返回长度（字节）
    model_config = {"env_prefix": "RABBITMQ_"}


def _expand_env_vars(value: str) -> str:
    """展开字符串中的 ${ENV_VAR} 环境变量引用"""
    return re.sub(r"\$\{(\w+)\}", lambda m: os.getenv(m.group(1), ""), value)


def load_clusters() -> Dict[str, ClusterConfig]:
    """从 config.yaml 加载所有集群配置，密码字段支持环境变量展开"""
    config_path = Path(__file__).parent.parent.parent / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    clusters = {}
    for name, cfg in raw.get("clusters", {}).items():
        # 展开密码中的 ${XXX} 环境变量
        cfg["password"] = _expand_env_vars(cfg.get("password", ""))
        clusters[name] = ClusterConfig(**cfg)
    return clusters
