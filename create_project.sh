#!/usr/bin/env bash
set -e
PROJ="rabbitmq-debug-mcp"
mkdir -p "$PROJ/src"
cd "$PROJ"

# 1. pyproject.toml
cat > pyproject.toml << 'PYEOF'
[project]
name = "rabbitmq-debug-mcp"
version = "1.0.0"
description = "RabbitMQ Debug & Visualization MCP Server (Python)"
requires-python = ">=3.10"
dependencies = [
  "mcp>=1.0.0",
  "httpx>=0.27.0",
  "pydantic>=2.0.0",
  "pydantic-settings>=2.0.0",
  "tenacity>=8.2.0",
  "pyyaml>=6.0",
  "python-dotenv>=1.0.0",
]

[project.scripts]
rmq-mcp = "src.server:main"

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"
PYEOF

# 2. .env.example
cat > .env.example << 'ENVEOF'
RMQ_DEFAULT_CLUSTER=dev
RABBITMQ_API_TIMEOUT=15
RABBITMQ_MAX_PAYLOAD=10240
ENVEOF

# 3. config.yaml
cat > config.yaml << 'CFGEOF'
clusters:
  dev:
    url: "http://localhost:15672"
    user: "guest"
    password: "guest"
    verify_ssl: false
  prod-east:
    url: "https://mq-east.internal:15672"
    user: "ai_debug_reader"
    password: "${PROD_RMQ_PASS}"
    verify_ssl: true
CFGEOF

# 4. src/__init__.py
touch src/__init__.py

# 5. src/config.py
cat > src/config.py << 'CEOF'
import os
import re
import yaml
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Dict

class ClusterConfig(BaseSettings):
    url: str
    user: str
    password: str
    verify_ssl: bool = True

class AppConfig(BaseSettings):
    default_cluster: str = "dev"
    api_timeout: int = 15
    max_payload: int = 10240

def _expand_env_vars(value: str) -> str:
    return re.sub(r"\$\{(\w+)\}", lambda m: os.getenv(m.group(1), ""), value)

def load_clusters() -> Dict[str, ClusterConfig]:
    config_path = Path(__file__).parent.parent / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    clusters = {}
    for name, cfg in raw.get("clusters", {}).items():
        cfg["password"] = _expand_env_vars(cfg.get("password", ""))
        clusters[name] = ClusterConfig(**cfg)
    return clusters
CEOF

# 6. src/api_client.py
cat > src/api_client.py << 'AEOF'
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from urllib.parse import quote
from .config import ClusterConfig, AppConfig

class RMQApiClient:
    def __init__(self, cluster_cfg: ClusterConfig, app_cfg: AppConfig):
        self.base_url = cluster_cfg.url.rstrip("/")
        self.auth = (cluster_cfg.user, cluster_cfg.password)
        self.verify = cluster_cfg.verify_ssl
        self.timeout = app_cfg.api_timeout
        self.max_payload = app_cfg.max_payload
        self._client = httpx.AsyncClient(verify=self.verify, timeout=self.timeout)

    def _vhost_path(self, vhost: str) -> str:
        return quote(vhost, safe="")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=5))
    async def request(self, method: str, path: str, **kwargs):
        resp = await self._client.request(method, f"{self.base_url}{path}", auth=self.auth, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        await self._client.aclose()

    async def get_vhosts(self):
        return await self.request("GET", "/api/vhosts")

    async def get_queues(self, vhost: str, pattern: str | None = None):
        vh = self._vhost_path(vhost)
        params = {"columns": "name,messages,messages_ready,messages_unacknowledged,consumers,node,state"}
        if pattern:
            params["pattern"] = pattern
        return await self.request("GET", f"/api/queues/{vh}", params=params)

    async def get_bindings(self, vhost: str, source: str | None = None, destination: str | None = None):
        vh = self._vhost_path(vhost)
        bindings = await self.request("GET", f"/api/bindings/{vh}")
        filtered = []
        for b in bindings:
            if source and b.get("source") != source: continue
            if destination and b.get("destination") != destination: continue
            filtered.append({
                "source": b["source"],
                "destination": b["destination"],
                "destination_type": b.get("destination_type", "queue"),
                "routing_key": b.get("routing_key", ""),
                "arguments": b.get("arguments", {})
            })
        return filtered

    async def peek_messages(self, vhost: str, queue: str, count: int, ackmode: str):
        vh = self._vhost_path(vhost)
        payload = {
            "count": min(count, 20),
            "ackmode": ackmode,
            "encoding": "auto",
            "truncate": self.max_payload
        }
        return await self.request("POST", f"/api/messages/{vh}/{quote(queue, safe='')}/get", json=payload)

    async def publish_message(self, vhost: str, exchange: str, routing_key: str, payload: str, headers: dict = None):
        vh = self._vhost_path(vhost)
        body = {
            "properties": {"headers": headers or {}},
            "routing_key": routing_key,
            "payload": payload,
            "payload_encoding": "auto"
        }
        return await self.request("POST", f"/api/exchanges/{vh}/{quote(exchange, safe='')}/publish", json=body)
AEOF

# 7. src/decoder.py
cat > src/decoder.py << 'DEOF'
import json
import xml.dom.minidom

def decode_message(raw_msg: dict, max_len: int) -> dict:
    enc = raw_msg.get("payload_encoding", "string")
    payload = raw_msg.get("payload", "")
    if enc == "json" or (isinstance(payload, str) and payload.strip().startswith("{")):
        try:
            decoded = json.loads(payload)
            payload = json.dumps(decoded, ensure_ascii=False, indent=2)
            enc = "json"
        except Exception: pass
    elif enc == "xml" or (isinstance(payload, str) and payload.strip().startswith("<")):
        try:
            parsed = xml.dom.minidom.parseString(payload)
            payload = parsed.toprettyxml(indent="  ")
            enc = "xml"
        except Exception: pass
    elif enc == "base64":
        payload = f"[BASE64] {payload[:50]}..."
    if len(str(payload)) > max_len:
        payload = str(payload)[:max_len] + f"\n...(truncated, use full_payload=true)"
    return {
        "routing_key": raw_msg.get("routing_key"),
        "properties": raw_msg.get("properties", {}),
        "headers": raw_msg.get("headers", {}),
        "payload": payload,
        "encoding": enc,
        "redelivered": raw_msg.get("redelivered", False),
        "message_id": raw_msg.get("properties", {}).get("message_id")
    }
DEOF

# 8. src/tools.py
cat > src/tools.py << 'TEOF'
from typing import Annotated, Dict, List
from pydantic import Field
from mcp.server.fastmcp import FastMCP
from .config import load_clusters, AppConfig
from .api_client import RMQApiClient
from .decoder import decode_message

mcp = FastMCP(name="rabbitmq-debug", version="1.0.0")
_clients: Dict[str, RMQApiClient] = {}
app_cfg = AppConfig()

def get_client(cluster_profile: str | None = None) -> RMQApiClient:
    profile = cluster_profile or app_cfg.default_cluster
    if profile not in _clients:
        clusters = load_clusters()
        if profile not in clusters:
            raise ValueError(f"Cluster '{profile}' not found. Available: {list(clusters.keys())}")
        _clients[profile] = RMQApiClient(clusters[profile], app_cfg)
    return _clients[profile]

@mcp.tool()
async def rmq_list_vhosts(cluster_profile: Annotated[str | None, Field(description="集群别名")] = None) -> List[Dict]:
    try:
        data = await get_client(cluster_profile).get_vhosts()
        return [{"name": v["name"], "messages": v.get("messages", 0), "status": v.get("state", "unknown")} for v in data]
    except Exception as e:
        return [{"error": str(e)}]

@mcp.tool()
async def rmq_list_queues(
    vhost: Annotated[str, Field(description="VHost 名称")] = "/",
    pattern: Annotated[str | None, Field(description="队列名称正则过滤")] = None,
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None
) -> List[Dict]:
    try:
        data = await get_client(cluster_profile).get_queues(vhost, pattern)
        return [{
            "name": q["name"],
            "messages": q.get("messages", 0),
            "messages_ready": q.get("messages_ready", 0),
            "messages_unack": q.get("messages_unacknowledged", 0),
            "consumers": q.get("consumers", 0),
            "node": q.get("node", "unknown"),
            "state": q.get("state", "unknown")
        } for q in data]
    except Exception as e:
        return [{"error": f"获取队列失败: {str(e)}"}]

@mcp.tool()
async def rmq_get_bindings(
    vhost: Annotated[str, Field(description="VHost 名称")] = "/",
    source: Annotated[str | None, Field(description="Source Exchange")] = None,
    destination: Annotated[str | None, Field(description="Queue/Exchange 名称")] = None,
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None
) -> List[Dict]:
    try:
        return await get_client(cluster_profile).get_bindings(vhost, source, destination)
    except Exception as e:
        return [{"error": f"获取绑定失败: {str(e)}"}]

@mcp.tool()
async def rmq_peek_messages(
    vhost: Annotated[str, Field(description="VHost 名称")] = "/",
    queue: Annotated[str, Field(description="队列名称")],
    count: Annotated[int, Field(ge=1, le=20)] = 5,
    ackmode: Annotated[str, Field(description="只读模式")] = "ack_requeue_true",
    full_payload: Annotated[bool, Field(description="是否返回完整 Payload")] = False,
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None
) -> List[Dict]:
    try:
        client = get_client(cluster_profile)
        raw_msgs = await client.peek_messages(vhost, queue, count, ackmode)
        max_len = app_cfg.max_payload * 100 if full_payload else app_cfg.max_payload
        return [decode_message(msg, max_len) for msg in raw_msgs]
    except Exception as e:
        return [{"error": f"预览失败: {str(e)}"}]

@mcp.tool()
async def rmq_publish_message(
    vhost: Annotated[str, Field(description="VHost")] = "/",
    exchange: Annotated[str, Field(description="交换机名称")],
    routing_key: Annotated[str, Field(description="路由键")],
    payload: Annotated[str, Field(description="消息体")],
    headers: Annotated[Dict, Field(description="自定义 Headers")] = {},
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None
) -> Dict:
    try:
        client = get_client(cluster_profile)
        res = await client.publish_message(vhost, exchange, routing_key, payload, headers)
        return {"routed": res.get("routed", False), "payload_bytes": len(payload)}
    except Exception as e:
        return {"error": f"发布失败: {str(e)}"}
TEOF

# 9. src/server.py
cat > src/server.py << 'SEOF'
import asyncio
import sys
from .tools import mcp, _clients
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def main():
    print("🚀 Starting RabbitMQ Debug MCP Server (stdio)...")
    try:
        mcp.run()
    except KeyboardInterrupt:
        print("\n👋 Shutting down gracefully...")
        loop = asyncio.new_event_loop()
        for client in _clients.values():
            loop.run_until_complete(client.close())
        sys.exit(0)

if __name__ == "__main__":
    main()
SEOF

# 10. Dockerfile
cat > Dockerfile << 'DEOF'
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .
COPY src/ ./src/
COPY config.yaml .env ./
RUN useradd -m rmquser && chown -R rmquser:rmquser /app
USER rmquser
ENTRYPOINT ["python", "-m", "src.server"]
DEOF

# 11. docker-compose.yml
cat > docker-compose.yml << 'COEOF'
services:
  rmq-mcp:
    build: .
    volumes:
      - ./config.yaml:/app/config.yaml:ro
      - ./.env:/app/.env:ro
    stdin_open: true
    tty: true
    environment:
      - PYTHONUNBUFFERED=1
COEOF

# 12. README.md
cat > README.md << 'REOF'
# 🐇 RabbitMQ Debug MCP Server
基于 Python + FastMCP 的 RabbitMQ 开发调试与消息可视化服务，支持多集群/多 VHost 隔离、安全 Peek、自动解码。

## 🚀 快速启动
```bash
cd rabbitmq-debug-mcp
cp .env.example .env
# 编辑 config.yaml 填入集群信息
pip install -e .
python -m src.server