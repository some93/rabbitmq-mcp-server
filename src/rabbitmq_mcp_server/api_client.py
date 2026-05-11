"""RabbitMQ HTTP Management API 异步客户端，封装所有集群交互接口"""

from urllib.parse import quote

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import AppConfig, ClusterConfig


class RMQApiClient:
    """RabbitMQ Management API 异步客户端

    使用 httpx 发起异步 HTTP 请求，内置重试机制（指数退避，最多 3 次）。
    vhost 在创建客户端时绑定，所有操作默认使用该 vhost，无需重复传入。
    """

    def __init__(self, cluster_cfg: ClusterConfig, app_cfg: AppConfig):
        self.base_url = cluster_cfg.url.rstrip("/")  # API 基础地址
        self.auth = (cluster_cfg.user, cluster_cfg.password)  # Basic Auth 凭据
        self.vhost = cluster_cfg.vhost  # 绑定的默认虚拟主机
        self._vh = quote(cluster_cfg.vhost, safe="")  # URL 编码后的 vhost 路径段
        self.verify = cluster_cfg.verify_ssl  # SSL 校验开关
        self.timeout = app_cfg.api_timeout  # 请求超时
        self.max_payload = app_cfg.max_payload  # 消息截断长度
        self._client = httpx.AsyncClient(verify=self.verify, timeout=self.timeout)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=5))
    async def request(self, method: str, path: str, **kwargs):
        """发送 HTTP 请求，失败时自动重试（最多 3 次，指数退避 1~5 秒）"""
        resp = await self._client.request(
            method, f"{self.base_url}{path}", auth=self.auth, **kwargs
        )
        resp.raise_for_status()
        return resp.json()

    async def close(self):
        """关闭 HTTP 客户端连接"""
        await self._client.aclose()

    # ======================== 集群 / 节点 ========================

    async def get_overview(self):
        """获取集群概览（版本、对象总数、消息总量等）"""
        return await self.request("GET", "/api/overview")

    async def get_nodes(self):
        """获取所有节点状态（内存、磁盘、文件描述符、进程数、告警等）"""
        return await self.request("GET", "/api/nodes")

    # ======================== VHost ========================

    async def get_vhosts(self):
        """获取所有虚拟主机列表"""
        return await self.request("GET", "/api/vhosts")

    # ======================== 队列 ========================

    async def get_queues(self, pattern: str | None = None):
        """获取当前 vhost 下的队列列表，支持名称正则过滤

        Args:
            pattern: 可选的队列名称正则表达式
        """
        params = {
            "columns": "name,messages,messages_ready,messages_unacknowledged,"
            "consumers,node,state,message_bytes,"
            "message_bytes_persistent,message_bytes_ram"
        }
        if pattern:
            params["pattern"] = pattern
        return await self.request("GET", f"/api/queues/{self._vh}", params=params)

    async def get_queue_detail(self, queue: str):
        """获取单个队列的完整详情（消费者、策略、参数、速率等）"""
        return await self.request("GET", f"/api/queues/{self._vh}/{quote(queue, safe='')}")

    async def purge_queue(self, queue: str):
        """清空队列中的所有消息"""
        return await self.request("POST", f"/api/queues/{self._vh}/{quote(queue, safe='')}/purge")

    # ======================== 交换机 ========================

    async def get_exchanges(self):
        """获取当前 vhost 下的所有交换机"""
        return await self.request("GET", f"/api/exchanges/{self._vh}")

    # ======================== 绑定 ========================

    async def get_bindings(self, source: str | None = None, destination: str | None = None):
        """获取绑定关系列表，支持按源交换机和目标队列/交换机过滤

        Args:
            source: 源交换机名称（可选）
            destination: 目标队列或交换机名称（可选）
        """
        bindings = await self.request("GET", f"/api/bindings/{self._vh}")
        # 客户端侧过滤，仅保留匹配的结果
        filtered = []
        for b in bindings:
            if source and b.get("source") != source:
                continue
            if destination and b.get("destination") != destination:
                continue
            filtered.append({
                "source": b["source"],
                "destination": b["destination"],
                "destination_type": b.get("destination_type", "queue"),
                "routing_key": b.get("routing_key", ""),
                "arguments": b.get("arguments", {}),
            })
        return filtered

    # ======================== 消费者 ========================

    async def get_consumers(self):
        """获取当前 vhost 下的所有消费者信息"""
        return await self.request("GET", f"/api/consumers/{self._vh}")

    # ======================== 连接 ========================

    async def get_connections(self):
        """获取所有客户端连接信息"""
        return await self.request("GET", "/api/connections")

    # ======================== Channel ========================

    async def get_channels(self):
        """获取所有 Channel 信息"""
        return await self.request("GET", "/api/channels")

    # ======================== 策略 ========================

    async def get_policies(self):
        """获取当前 vhost 下的策略列表（TTL、DLX、HA 模式等）"""
        return await self.request("GET", f"/api/policies/{self._vh}")

    # ======================== 消息操作 ========================

    async def peek_messages(self, queue: str, count: int, ackmode: str):
        """预览队列中的消息（不会删除消息）

        Args:
            queue: 队列名称
            count: 预览条数，最多 20 条
            ackmode: 确认模式
                - ack_requeue_true: 确认后重新入队（只读预览）
                - ack_requeue_false: 确认后不重新入队（消费掉）
                - reject_requeue_true: 拒绝后重新入队
        """
        payload = {
            "count": min(count, 20),  # API 限制最多 20 条
            "ackmode": ackmode,
            "encoding": "auto",
            "truncate": self.max_payload,  # 截断过长的消息体
        }
        return await self.request(
            "POST",
            f"/api/messages/{self._vh}/{quote(queue, safe='')}/get",
            json=payload,
        )

    async def publish_message(
        self, exchange: str, routing_key: str, payload: str, headers: dict | None = None
    ):
        """发布消息到指定交换机

        Args:
            exchange: 交换机名称
            routing_key: 路由键
            payload: 消息体内容
            headers: 自定义消息头（可选）
        """
        body = {
            "properties": {"headers": headers or {}},
            "routing_key": routing_key,
            "payload": payload,
            "payload_encoding": "auto",
        }
        return await self.request(
            "POST",
            f"/api/exchanges/{self._vh}/{quote(exchange, safe='')}/publish",
            json=body,
        )

    # ======================== 拓扑定义 ========================

    async def get_definitions(self):
        """导出完整集群拓扑定义（exchanges, queues, bindings, policies, users, vhosts）"""
        return await self.request("GET", "/api/definitions")
