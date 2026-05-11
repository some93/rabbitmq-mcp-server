"""MCP 工具定义模块：注册所有 RabbitMQ 调试工具到 FastMCP 服务"""

from typing import Annotated, Dict, List

from pydantic import Field

from mcp.server.fastmcp import FastMCP

from .api_client import RMQApiClient
from .config import AppConfig, load_clusters
from .decoder import decode_message

# FastMCP 服务实例
mcp = FastMCP(name="rabbitmq-mcp-server")

# 已创建的 API 客户端缓存（按集群别名复用）
_clients: Dict[str, RMQApiClient] = {}

# 应用全局配置
app_cfg = AppConfig()


def get_client(cluster_profile: str | None = None) -> RMQApiClient:
    """获取指定集群的 API 客户端（懒加载 + 缓存）

    Args:
        cluster_profile: 集群别名，为空时使用默认集群
    """
    profile = cluster_profile or app_cfg.default_cluster
    if profile not in _clients:
        clusters = load_clusters()
        if profile not in clusters:
            raise ValueError(f"Cluster '{profile}' not found. Available: {list(clusters.keys())}")
        _clients[profile] = RMQApiClient(clusters[profile], app_cfg)
    return _clients[profile]


# ============================================================
# Tier 1 - 核心工具
# ============================================================


@mcp.tool()
async def rmq_cluster_overview(
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> Dict:
    """获取集群概览：版本、节点、内存、磁盘、告警

    返回集群健康状态的关键指标，包括 RabbitMQ/Erlang 版本、
    各节点的内存/磁盘使用、文件描述符、进程数以及活跃告警。
    """
    try:
        client = get_client(cluster_profile)
        overview = await client.get_overview()
        nodes = await client.get_nodes()
        # 整理节点摘要信息
        node_summary = []
        for n in nodes:
            node_summary.append({
                "name": n.get("name"),
                "status": n.get("status"),
                "uptime_ms": n.get("uptime"),
                "mem_used": n.get("mem_used"),
                "mem_limit": n.get("mem_limit"),
                "disk_free": n.get("disk_free"),
                "disk_free_limit": n.get("disk_free_limit"),
                "fd_used": n.get("fd_used"),
                "fd_total": n.get("fd_total"),
                "proc_used": n.get("proc_used"),
                "proc_total": n.get("proc_total"),
                "alarms": n.get("alarms", []),
            })
        return {
            "cluster_name": overview.get("cluster_name"),
            "rabbitmq_version": overview.get("rabbitmq_version"),
            "erlang_version": overview.get("erlang_version"),
            "object_totals": overview.get("object_totals", {}),
            "message_totals": {
                "messages": overview.get("queue_totals", {}).get("messages"),
                "messages_ready": overview.get("queue_totals", {}).get("messages_ready"),
                "messages_unacknowledged": overview.get("queue_totals", {}).get("messages_unacknowledged"),
            },
            "nodes": node_summary,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def rmq_list_vhosts(
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> List[Dict]:
    """列出所有 VHost（虚拟主机）"""
    try:
        data = await get_client(cluster_profile).get_vhosts()
        return [
            {"name": v["name"], "messages": v.get("messages", 0), "status": v.get("state", "unknown")}
            for v in data
        ]
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
async def rmq_list_queues(
    vhost: Annotated[str, Field(description="VHost 名称")] = "/",
    pattern: Annotated[str | None, Field(description="队列名称正则过滤")] = None,
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> List[Dict]:
    """列出 VHost 下所有队列及消息统计

    返回每个队列的消息总数、待消费数、未确认数、消费者数、所在节点和状态。
    """
    try:
        data = await get_client(cluster_profile).get_queues(vhost, pattern)
        return [
            {
                "name": q["name"],
                "messages": q.get("messages", 0),
                "messages_ready": q.get("messages_ready", 0),
                "messages_unack": q.get("messages_unacknowledged", 0),
                "consumers": q.get("consumers", 0),
                "node": q.get("node", "unknown"),
                "state": q.get("state", "unknown"),
                "message_bytes": q.get("message_bytes", 0),
            }
            for q in data
        ]
    except Exception as e:
        return [{"error": f"获取队列失败: {e}"}]


@mcp.tool()
async def rmq_queue_detail(
    queue: Annotated[str, Field(description="队列名称")],
    vhost: Annotated[str, Field(description="VHost 名称")] = "/",
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> Dict:
    """获取单个队列完整详情：消费者列表、策略、参数、消息速率等"""
    try:
        return await get_client(cluster_profile).get_queue_detail(vhost, queue)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def rmq_list_exchanges(
    vhost: Annotated[str, Field(description="VHost 名称")] = "/",
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> List[Dict]:
    """列出 VHost 下所有交换机（类型、持久化、自动删除、参数）"""
    try:
        data = await get_client(cluster_profile).get_exchanges(vhost)
        return [
            {
                "name": e["name"],
                "type": e.get("type"),
                "durable": e.get("durable"),
                "auto_delete": e.get("auto_delete"),
                "arguments": e.get("arguments", {}),
            }
            for e in data
        ]
    except Exception as e:
        return [{"error": f"获取交换机失败: {e}"}]


@mcp.tool()
async def rmq_list_bindings(
    vhost: Annotated[str, Field(description="VHost 名称")] = "/",
    source: Annotated[str | None, Field(description="Source Exchange")] = None,
    destination: Annotated[str | None, Field(description="Queue/Exchange 名称")] = None,
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> List[Dict]:
    """列出绑定关系，可按源交换机或目标队列/交换机过滤"""
    try:
        return await get_client(cluster_profile).get_bindings(vhost, source, destination)
    except Exception as e:
        return [{"error": f"获取绑定失败: {e}"}]


@mcp.tool()
async def rmq_list_consumers(
    vhost: Annotated[str, Field(description="VHost 名称")] = "/",
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> List[Dict]:
    """列出 VHost 下所有消费者（队列、Channel、连接、ACK 模式、预取数）"""
    try:
        data = await get_client(cluster_profile).get_consumers(vhost)
        return [
            {
                "queue": c.get("queue", {}).get("name"),
                "channel": c.get("channel", {}).get("name"),
                "connection": c.get("connection", {}).get("name"),
                "consumer_tag": c.get("consumer_tag"),
                "ack_required": c.get("ack_required"),
                "prefetch_count": c.get("prefetch_count"),
                "arguments": c.get("arguments", {}),
            }
            for c in data
        ]
    except Exception as e:
        return [{"error": f"获取消费者失败: {e}"}]


@mcp.tool()
async def rmq_list_connections(
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> List[Dict]:
    """列出所有客户端连接（用户、来源 IP、协议、Channel 数、状态）"""
    try:
        data = await get_client(cluster_profile).get_connections()
        return [
            {
                "name": c.get("name"),
                "user": c.get("user"),
                "peer_host": c.get("peer_host"),
                "peer_port": c.get("peer_port"),
                "protocol": c.get("protocol"),
                "channels": c.get("channels"),
                "state": c.get("state"),
                "client_properties": c.get("client_properties", {}),
            }
            for c in data
        ]
    except Exception as e:
        return [{"error": f"获取连接失败: {e}"}]


@mcp.tool()
async def rmq_peek_messages(
    queue: Annotated[str, Field(description="队列名称")],
    vhost: Annotated[str, Field(description="VHost 名称")] = "/",
    count: Annotated[int, Field(description="预览条数", ge=1, le=20)] = 5,
    ackmode: Annotated[str, Field(description="确认模式: ack_requeue_true / ack_requeue_false / reject_requeue_true")] = "ack_requeue_true",
    full_payload: Annotated[bool, Field(description="是否返回完整 Payload")] = False,
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> List[Dict]:
    """安全预览队列中的消息（不删除消息）

    默认使用 ack_requeue_true 模式，消息预览后重新入队。
    消息体会自动解码（JSON/XML/Base64 等）。
    """
    try:
        client = get_client(cluster_profile)
        raw_msgs = await client.peek_messages(vhost, queue, count, ackmode)
        # full_payload=true 时返回 100 倍长度，否则使用默认限制
        max_len = app_cfg.max_payload * 100 if full_payload else app_cfg.max_payload
        return [decode_message(msg, max_len) for msg in raw_msgs]
    except Exception as e:
        return [{"error": f"预览失败: {e}"}]


@mcp.tool()
async def rmq_publish_message(
    exchange: Annotated[str, Field(description="交换机名称")],
    routing_key: Annotated[str, Field(description="路由键")],
    payload: Annotated[str, Field(description="消息体")],
    vhost: Annotated[str, Field(description="VHost")] = "/",
    headers: Annotated[Dict, Field(description="自定义 Headers")] = {},
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> Dict:
    """发布消息到指定交换机"""
    try:
        client = get_client(cluster_profile)
        res = await client.publish_message(vhost, exchange, routing_key, payload, headers)
        return {"routed": res.get("routed", False), "payload_bytes": len(payload)}
    except Exception as e:
        return {"error": f"发布失败: {e}"}


# ============================================================
# Tier 2 - 高级调试工具
# ============================================================


@mcp.tool()
async def rmq_list_policies(
    vhost: Annotated[str, Field(description="VHost 名称")] = "/",
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> List[Dict]:
    """列出 VHost 下所有策略（TTL、DLX、HA 模式、最大长度等）

    策略是 RabbitMQ 的核心配置机制，控制消息存活时间、死信路由、
    镜像队列等行为，对调试问题至关重要。
    """
    try:
        data = await get_client(cluster_profile).get_policies(vhost)
        return [
            {
                "name": p.get("name"),
                "pattern": p.get("pattern"),
                "apply_to": p.get("apply-to"),
                "definition": p.get("definition", {}),
                "priority": p.get("priority"),
            }
            for p in data
        ]
    except Exception as e:
        return [{"error": f"获取策略失败: {e}"}]


@mcp.tool()
async def rmq_list_channels(
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> List[Dict]:
    """列出所有 Channel（连接、用户、预取数、未确认消息数、消费者数）"""
    try:
        data = await get_client(cluster_profile).get_channels()
        return [
            {
                "name": ch.get("name"),
                "connection": ch.get("connection", {}).get("name"),
                "user": ch.get("user"),
                "vhost": ch.get("vhost"),
                "prefetch_count": ch.get("prefetch_count"),
                "messages_unacknowledged": ch.get("messages_unacknowledged"),
                "consumer_count": ch.get("consumer_count"),
            }
            for ch in data
        ]
    except Exception as e:
        return [{"error": f"获取 Channel 失败: {e}"}]


@mcp.tool()
async def rmq_trace_route(
    vhost: Annotated[str, Field(description="VHost 名称")] = "/",
    exchange: Annotated[str, Field(description="交换机名称")] = "",
    routing_key: Annotated[str, Field(description="路由键")] = "",
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> Dict:
    """模拟路由追踪：根据绑定关系预测消息会到达哪些队列

    通过查询绑定关系，在本地模拟 RabbitMQ 的路由逻辑（支持 * 和 # 通配符），
    递归追踪 exchange-to-exchange 绑定（最多 5 层）。
    用于排查"消息为什么没到目标队列"的问题。
    """
    try:
        client = get_client(cluster_profile)

        def _match_routing_key(pattern: str, key: str) -> bool:
            """匹配 routing key，支持 *（单段）和 #（多段）通配符"""
            if pattern == key:
                return True
            pat_parts = pattern.split(".")
            key_parts = key.split(".")
            if len(pat_parts) != len(key_parts):
                return False
            for p, k in zip(pat_parts, key_parts):
                if p == "#":
                    return True
                if p == "*" or p == k:
                    continue
                return False
            return False

        def _match_headers(args: dict, headers: dict) -> bool:
            """匹配 header 绑定参数（x-match: all/any）"""
            if not args or "x-match" not in args:
                return True
            mode = args["x-match"]
            for hk, hv in args.items():
                if hk == "x-match":
                    continue
                if hk not in headers:
                    if mode == "all":
                        return False
                    continue
                if headers[hk] != hv:
                    if mode == "all":
                        return False
            return True

        async def _trace(exchange_name: str, rk: str, depth: int = 0) -> list:
            """递归追踪绑定链，depth 限制防止无限循环"""
            if depth > 5:
                return []
            bindings = await client.get_bindings(vhost, source=exchange_name)
            targets = []
            for b in bindings:
                if not _match_routing_key(b.get("routing_key", ""), rk):
                    continue
                if not _match_headers(b.get("arguments", {}), {}):
                    continue
                dest = b["destination"]
                dest_type = b.get("destination_type", "queue")
                if dest_type == "queue":
                    targets.append({"type": "queue", "name": dest})
                elif dest_type == "exchange":
                    # 递归追踪 exchange-to-exchange 绑定
                    sub = await _trace(dest, rk, depth + 1)
                    targets.append({"type": "exchange", "name": dest, "routes_to": sub})
            return targets

        routes = await _trace(exchange, routing_key)
        return {
            "exchange": exchange,
            "routing_key": routing_key,
            "routes": routes,
            "would_reach_queues": [t["name"] for t in routes if t["type"] == "queue"],
        }
    except Exception as e:
        return {"error": f"路由追踪失败: {e}"}


@mcp.tool()
async def rmq_purge_queue(
    queue: Annotated[str, Field(description="队列名称")],
    vhost: Annotated[str, Field(description="VHost 名称")] = "/",
    confirm: Annotated[bool, Field(description="确认清空操作")] = False,
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> Dict:
    """清空队列中的所有消息（需要设置 confirm=true 确认）

    危险操作：清空后消息不可恢复。请确保在生产环境中谨慎使用。
    """
    if not confirm:
        return {"error": "请设置 confirm=true 以确认清空操作"}
    try:
        result = await get_client(cluster_profile).purge_queue(vhost, queue)
        return {"purged": result.get("message_count", 0)}
    except Exception as e:
        return {"error": f"清空失败: {e}"}


@mcp.tool()
async def rmq_export_definitions(
    cluster_profile: Annotated[str | None, Field(description="集群别名")] = None,
) -> Dict:
    """导出完整集群拓扑定义（exchanges, queues, bindings, policies, users, vhosts）

    用于备份集群配置或在调试会话间共享拓扑快照。
    """
    try:
        return await get_client(cluster_profile).get_definitions()
    except Exception as e:
        return {"error": f"导出失败: {e}"}
