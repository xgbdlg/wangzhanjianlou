# services/ws_broadcast.py
# 前端 WebSocket 广播服务：向 Chrome 插件推送实时事件

import asyncio
import json
import logging
from typing import Any

import websockets
from websockets.server import WebSocketServerProtocol

logger = logging.getLogger("ws_broadcast")

# 已连接的前端客户端集合
_connected_clients: set[WebSocketServerProtocol] = set()


async def ws_handler(websocket: WebSocketServerProtocol) -> None:
    """处理前端 WebSocket 连接。"""
    _connected_clients.add(websocket)
    logger.info("前端 WS 客户端已连接 (在线=%d)", len(_connected_clients))
    try:
        async for _ in websocket:
            pass  # 前端客户端只接收推送，不发送消息
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        _connected_clients.discard(websocket)
        logger.info("前端 WS 客户端已断开 (在线=%d)", len(_connected_clients))


async def broadcast(event_type: str, data: Any) -> None:
    """向所有已连接的前端客户端广播消息。

    Args:
        event_type: 事件名 (deal_alert / stop_loss / auction_update / engine_status)
        data: 事件数据（dict 或可序列化对象）
    """
    if not _connected_clients:
        return

    message = json.dumps({"event": event_type, "data": data}, ensure_ascii=False, default=str)
    dead: set = set()

    for ws in _connected_clients:
        try:
            await ws.send(message)
        except websockets.exceptions.ConnectionClosed:
            dead.add(ws)
        except Exception as exc:
            logger.warning("WS 推送失败: %s", exc)
            dead.add(ws)

    _connected_clients.difference_update(dead)


def get_client_count() -> int:
    """获取当前在线前端客户端数。"""
    return len(_connected_clients)
