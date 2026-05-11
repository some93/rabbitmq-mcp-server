# RabbitMQ MCP Server

RabbitMQ 调试与消息可视化 MCP Server，支持多集群/多 VHost、双传输模式 (stdio + HTTP/SSE)。

## 安装

```bash
# 方式 1: uvx (推荐)
uvx rabbitmq-mcp-server

# 方式 2: pip
pip install -e .

# 方式 3: Docker
docker compose up
```

## 配置

编辑 `config.yaml` 添加集群信息：

```yaml
clusters:
  dev:
    url: "http://localhost:15672"
    user: "guest"
    password: "guest"
    vhost: "/"
    verify_ssl: false
  prod:
    url: "https://mq.internal:15672"
    user: "ai_reader"
    password: "${PROD_RMQ_PASS}"  # 支持环境变量
    vhost: "myapp"
    verify_ssl: true
```

环境变量：
- `RMQ_DEFAULT_CLUSTER` - 默认集群别名 (默认: dev)
- `RMQ_MCP_TRANSPORT` - 传输模式: stdio / http / sse
- `RABBITMQ_API_TIMEOUT` - API 超时 (默认: 15s)

## 使用

### stdio 模式 (Claude Desktop / Cursor)

```bash
rabbitmq-mcp-server
# 或
python -m rabbitmq_mcp_server.server
```

Claude Desktop 配置:
```json
{
  "mcpServers": {
    "RabbitMQ": {
      "command": "uvx",
      "args": ["rabbitmq-mcp-server"]
    }
  }
}
```

### HTTP/SSE 模式 (远程访问)

```bash
rabbitmq-mcp-server --transport http --port 9000
```

## 提供的 Tools

### 核心工具

| Tool | 说明 |
|---|---|
| `rmq_cluster_overview` | 集群概览: 版本、节点、内存、磁盘、告警 |
| `rmq_list_vhosts` | 列出所有 VHost |
| `rmq_list_queues` | 列出队列及消息统计 |
| `rmq_queue_detail` | 单队列完整详情 |
| `rmq_list_exchanges` | 列出交换机 |
| `rmq_list_bindings` | 列出绑定关系 |
| `rmq_list_consumers` | 列出消费者 |
| `rmq_list_connections` | 列出连接 |
| `rmq_peek_messages` | 安全预览消息 |
| `rmq_publish_message` | 发布消息 |

### 高级调试

| Tool | 说明 |
|---|---|
| `rmq_list_policies` | 策略列表 (TTL, DLX, HA) |
| `rmq_list_channels` | Channel 列表 |
| `rmq_trace_route` | 路由追踪模拟 |
| `rmq_purge_queue` | 清空队列 |
| `rmq_export_definitions` | 导出完整拓扑 |
