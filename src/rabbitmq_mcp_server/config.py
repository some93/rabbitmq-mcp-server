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
    vhost: str = "/"  # 默认虚拟主机，连接时绑定，所有操作默认使用此 vhost
    verify_ssl: bool = True  # 是否校验 SSL 证书


class AppConfig(BaseSettings):
    """应用全局配置，优先从环境变量读取 (前缀: RABBITMQ_)"""

    default_cluster: str = "dev"  # 环境变量: RABBITMQ_DEFAULT_CLUSTER
    api_timeout: int = 15  # Management API 请求超时（秒）
    max_payload: int = 10240  # 消息体最大返回长度（字节）
    model_config = {"env_prefix": "RABBITMQ_"}


def _expand_env_vars(value: str) -> str:
    """展开字符串中的 ${ENV_VAR} 环境变量引用"""
    return re.sub(r"\$\{(\w+)\}", lambda m: os.getenv(m.group(1), ""), value)


def _find_config() -> Path | None:
    """按优先级查找 config.yaml

    1. 环境变量 RMQ_CONFIG_PATH 指定的路径
    2. 当前工作目录 ./config.yaml
    3. 项目根目录（源码开发时使用）
    """
    # 优先级 1: 环境变量显式指定
    env_path = os.getenv("RMQ_CONFIG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
    # 优先级 2: 当前工作目录
    cwd_path = Path.cwd() / "config.yaml"
    if cwd_path.exists():
        return cwd_path
    # 优先级 3: 源码项目根目录（开发模式）
    pkg_path = Path(__file__).parent.parent.parent / "config.yaml"
    if pkg_path.exists():
        return pkg_path
    return None


def load_clusters() -> Dict[str, ClusterConfig]:
    """从 config.yaml 加载所有集群配置，密码字段支持环境变量展开"""
    config_path = _find_config()
    if config_path is None:
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    clusters = {}
    for name, cfg in raw.get("clusters", {}).items():
        # 展开密码中的 ${XXX} 环境变量
        cfg["password"] = _expand_env_vars(cfg.get("password", ""))
        clusters[name] = ClusterConfig(**cfg)
    return clusters
