"""MCP 服务入口：支持 stdio / HTTP / SSE 三种传输模式"""

import argparse
import asyncio
import logging
import os
import sys

from .tools import mcp, _clients

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rabbitmq-mcp-server")


def main():
    """服务启动入口

    支持的传输模式：
    - stdio: 标准输入输出，适用于 Claude Desktop / Cursor / uvx 本地调用
    - http:  Streamable HTTP，适用于远程访问和 Web 集成（FastMCP v3 推荐）
    - sse:   Server-Sent Events，适用于兼容旧版 MCP 客户端

    配置优先级：命令行参数 > 环境变量 > 默认值
    """
    parser = argparse.ArgumentParser(description="RabbitMQ Debug MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        default=os.getenv("RMQ_MCP_TRANSPORT", "stdio"),
        help="传输模式 (默认: stdio)",
    )
    parser.add_argument("--host", default=os.getenv("RMQ_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("RMQ_MCP_PORT", "9000")))
    args = parser.parse_args()

    logger.info(f"Starting RabbitMQ MCP Server (transport={args.transport})")

    try:
        if args.transport == "stdio":
            # stdio 模式：通过标准输入输出通信，适用于本地 MCP 客户端
            mcp.run()
        elif args.transport == "http":
            # HTTP 模式：Streamable HTTP 传输（FastMCP v3 推荐）
            mcp.run(transport="http", host=args.host, port=args.port)
        elif args.transport == "sse":
            # SSE 模式：Server-Sent Events（兼容旧版客户端）
            mcp.run(transport="sse", host=args.host, port=args.port)
    except KeyboardInterrupt:
        # 优雅关闭：关闭所有已建立的 API 客户端连接
        logger.info("Shutting down...")
        loop = asyncio.new_event_loop()
        for client in _clients.values():
            loop.run_until_complete(client.close())
        sys.exit(0)


if __name__ == "__main__":
    main()
