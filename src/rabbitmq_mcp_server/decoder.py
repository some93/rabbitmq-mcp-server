"""消息解码模块：支持 JSON、XML、Base64、gzip 压缩等格式的自动检测与解码"""

import base64
import gzip
import io
import json
import xml.dom.minidom


def _hex_dump(data: bytes, width: int = 16) -> str:
    """将二进制数据格式化为 hex dump 视图（类似 xxd 命令输出）"""
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        # 每字节用两位十六进制表示
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        # 可打印字符保留原样，不可打印用 . 替代
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{i:08x}  {hex_part:<{width * 3}}  {ascii_part}")
    return "\n".join(lines)


def _try_decompress(data: bytes) -> bytes:
    """尝试解压 gzip 格式数据，通过魔数 (0x1F 0x8B) 检测"""
    if len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B:
        try:
            return gzip.decompress(data)
        except Exception:
            pass
    return data


def _try_decode_bytes(data: bytes, max_len: int) -> tuple[str, str]:
    """尝试解码二进制数据：先解压，再依次尝试 UTF-8、JSON、XML，最后返回 hex dump"""
    data = _try_decompress(data)
    # 尝试 UTF-8 解码
    try:
        text = data.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return _hex_dump(data), "binary"
    # 依次尝试 JSON 和 XML 解析
    for decoder, label in [
        (json.loads, "json"),
        (lambda s: xml.dom.minidom.parseString(s).toprettyxml(indent="  "), "xml"),
    ]:
        try:
            return decoder(text), label
        except Exception:
            continue
    return text, "text"


def decode_message(raw_msg: dict, max_len: int) -> dict:
    """解码 RabbitMQ 消息，返回结构化的消息字典

    解码策略（按优先级）：
    1. Base64 编码：解码后尝试 UTF-8 -> JSON -> XML -> hex dump
    2. Content-Type 为 JSON 或 payload 以 { 开头：按 JSON 解析
    3. Content-Type 为 XML 或 payload 以 < 开头：按 XML 格式化
    4. 其他：保持原样

    Args:
        raw_msg: RabbitMQ Management API 返回的原始消息字典
        max_len: 消息体最大返回长度，超出则截断

    Returns:
        包含 routing_key、properties、headers、payload、encoding 等字段的字典
    """
    enc = raw_msg.get("payload_encoding", "string")
    payload = raw_msg.get("payload", "")
    # 从消息属性中读取 content_type 作为解码提示
    content_type = raw_msg.get("properties", {}).get("content_type", "")

    if enc == "base64":
        # Base64 编码：解码 -> 解压 -> 自动检测格式
        try:
            decoded = base64.b64decode(payload)
            decoded = _try_decompress(decoded)
            payload, enc = _try_decode_bytes(decoded, max_len)
        except Exception:
            payload = f"[BASE64] {str(payload)[:50]}..."
            enc = "base64_error"
    elif content_type in ("application/json", "text/json") or (
        isinstance(payload, str) and payload.strip().startswith("{")
    ):
        # JSON 消息：格式化输出
        try:
            decoded = json.loads(payload)
            payload = json.dumps(decoded, ensure_ascii=False, indent=2)
            enc = "json"
        except Exception:
            pass
    elif content_type in ("application/xml", "text/xml") or (
        isinstance(payload, str) and payload.strip().startswith("<")
    ):
        # XML 消息：格式化缩进输出
        try:
            parsed = xml.dom.minidom.parseString(payload)
            payload = parsed.toprettyxml(indent="  ")
            enc = "xml"
        except Exception:
            pass

    # 超长消息截断，提示使用 full_payload=true 获取完整内容
    if len(str(payload)) > max_len:
        payload = str(payload)[:max_len] + "\n...(truncated, use full_payload=true)"

    return {
        "routing_key": raw_msg.get("routing_key"),
        "properties": raw_msg.get("properties", {}),
        "headers": raw_msg.get("headers", {}),
        "payload": payload,
        "encoding": enc,
        "redelivered": raw_msg.get("redelivered", False),
        "message_id": raw_msg.get("properties", {}).get("message_id"),
    }
