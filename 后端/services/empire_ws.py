# services/empire_ws.py
# CSGOEmpire WebSocket 客户端，实现 Socket.IO Engine.IO v3 协议
#
# ⚠️ 注意：认证消息格式基于 Socket.IO 标准协议推测，
# 实际需通过浏览器 DevTools 抓包确认。

import asyncio
import json
import logging
from typing import Any, Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger("empire_ws")

# 连接配置
WS_URL = "wss://csgoempire.com/socket.io/?EIO=3&transport=websocket"
HEARTBEAT_INTERVAL = 25         # 心跳间隔（秒）
RECONNECT_DELAY_BASE = 5.0      # 重连基础延迟
MAX_RECONNECTS = 10             # 最大重连次数
PING_TIMEOUT = 10               # ping 超时秒数


class EmpireWSError(Exception):
    """Empire WebSocket 异常。"""
    pass


class EmpireWebSocketClient:
    """CSGOEmpire WebSocket 实时数据客户端。

    实现 Socket.IO over Engine.IO v3 协议：
      - 自动连接和认证
      - 心跳保活
      - 断线自动重连
      - 消息解析和回调

    事件回调类型: Callable[[str, dict], Awaitable[None]]
      回调收到 (event_name, data_dict)

    使用方式:
        async def on_event(event: str, data: dict):
            print(f"[{event}] {data}")

        ws = EmpireWebSocketClient(api_key="...", on_message=on_event)
        await ws.connect()
        # ... 保持运行 ...
        await ws.disconnect()
    """

    def __init__(
        self,
        api_key: str,
        on_message: Callable[[str, dict], Any],
        user_id: Optional[str] = None,
    ):
        self.api_key = api_key
        self.user_id = user_id
        self.on_message = on_message
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = False
        self._reconnect_count = 0
        self._should_stop = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._ping_interval = HEARTBEAT_INTERVAL  # 可由服务器握手更新

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    # ────────────────────────── 公开方法 ──────────────────────────

    async def connect(self) -> None:
        """建立 WebSocket 连接并完成认证。

        流程:
          1. TCP + TLS 连接
          2. Engine.IO 握手 (服务器发 0{...})
          3. Socket.IO connect (发 40)
          4. 发送认证消息 (42["identify", {...}])
          5. 启动心跳和监听循环
        """
        self._should_stop = False
        self._reconnect_count = 0
        await self._do_connect()
        self._start_tasks()

    async def disconnect(self) -> None:
        """断开 WebSocket 连接并清理资源。"""
        self._should_stop = True
        await self._cleanup()

    async def send_event(self, event: str, data: dict = None) -> None:
        """发送 Socket.IO 事件到服务器。

        格式: 42["eventName", {data}]
        """
        if not self.is_connected:
            raise EmpireWSError("WebSocket 未连接")
        payload = json.dumps([event, data or {}], ensure_ascii=False)
        frame = f"42{payload}"
        await self._ws.send(frame)  # type: ignore[union-attr]
        logger.debug("→ %s", frame[:200])

    # ────────────────────────── 内部：连接管理 ──────────────────────────

    async def _do_connect(self) -> None:
        """建立单次连接（不含重试逻辑）。"""
        try:
            logger.info("正在连接 Empire WebSocket: %s", WS_URL)
            self._ws = await websockets.connect(
                WS_URL,
                ping_interval=None,        # 我们自己管理心跳
                ping_timeout=None,
                close_timeout=5,
                max_size=2 ** 20,          # 1MB 最大消息
            )
            self._connected = True
            self._reconnect_count = 0
            logger.info("Empire WebSocket 已连接")
        except Exception as exc:
            logger.error("Empire WebSocket 连接失败: %s", exc)
            raise EmpireWSError(f"WebSocket 连接失败: {exc}") from exc

    async def _reconnect(self) -> bool:
        """尝试重连，返回是否成功。"""
        if self._should_stop:
            return False
        if self._reconnect_count >= MAX_RECONNECTS:
            logger.error("已达最大重连次数 (%d)，停止重连", MAX_RECONNECTS)
            return False

        self._reconnect_count += 1
        delay = RECONNECT_DELAY_BASE * min(self._reconnect_count, 6)
        logger.info("等待 %.0fs 后进行第 %d/%d 次重连...", delay, self._reconnect_count, MAX_RECONNECTS)
        await asyncio.sleep(delay)

        if self._should_stop:
            return False

        try:
            await self._cleanup()
            await self._do_connect()
            self._start_tasks()
            return True
        except Exception as exc:
            logger.warning("重连失败 (%d/%d): %s", self._reconnect_count, MAX_RECONNECTS, exc)
            return False

    # ────────────────────────── 内部：任务管理 ──────────────────────────

    def _start_tasks(self) -> None:
        """启动后台任务：心跳 + 消息监听。"""
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._listen_task = asyncio.create_task(self._listen_loop())

    async def _cleanup(self) -> None:
        """清理连接和任务。"""
        self._connected = False
        for task in [self._heartbeat_task, self._listen_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._heartbeat_task = None
        self._listen_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ────────────────────────── 内部：心跳 ──────────────────────────

    async def _heartbeat_loop(self) -> None:
        """Engine.IO 心跳：每 ping_interval 秒发送 ping(2)。"""
        await asyncio.sleep(1)  # 等连接稳定
        while self._connected and self._ws:
            try:
                await asyncio.wait_for(
                    self._ws.send("2"),
                    timeout=PING_TIMEOUT,
                )
                logger.debug("ping →")
            except (asyncio.TimeoutError, ConnectionClosed, WebSocketException) as exc:
                logger.warning("心跳失败: %s", exc)
                break
            except Exception as exc:
                logger.error("心跳异常: %s", exc)
                break
            await asyncio.sleep(self._ping_interval)

        # 心跳退出 = 连接断开，尝试重连
        if not self._should_stop:
            logger.warning("心跳检测到断线，开始重连...")
            success = await self._reconnect()
            if not success and not self._should_stop:
                logger.error("重连失败，WebSocket 已断开")

    # ────────────────────────── 内部：消息监听 ──────────────────────────

    async def _listen_loop(self) -> None:
        """消息接收循环：解析 EIO/SIO 帧并处理。"""
        while self._connected and self._ws:
            try:
                raw = await self._ws.recv()
            except ConnectionClosed as exc:
                logger.warning("WebSocket 连接关闭: %s", exc)
                break
            except WebSocketException as exc:
                logger.warning("WebSocket 异常: %s", exc)
                break

            try:
                self._handle_frame(raw)
            except Exception as exc:
                logger.error("消息处理异常: %s\n原始消息: %s", exc, str(raw)[:500])

        # 监听退出 = 连接断开
        self._connected = False
        if not self._should_stop:
            logger.warning("监听循环检测到断线，开始重连...")
            success = await self._reconnect()
            if not success and not self._should_stop:
                logger.error("重连失败，WebSocket 已断开")

    # ────────────────────────── 消息解析 ──────────────────────────

    def _handle_frame(self, raw: str) -> None:
        """解析 Engine.IO / Socket.IO 帧并派发事件。

        Engine.IO v3 帧格式:
          "0{...}"      → open (握手数据，含 pingInterval 等)
          "1"           → close
          "2"           → ping (心跳请求)
          "3"           → pong (心跳响应)
          "4{...}"      → message (含 Socket.IO 子协议)

        Socket.IO 子协议 (在 EIO message 之后):
          "40"          → connect to namespace
          "40{...}"     → connect with payload
          "41"          → disconnect
          "42[...]"     → event (如 42["auction_started", {...}])
          "43[...]"     → event with ack
        """
        if not raw:
            return

        eio_type = raw[0]

        # Engine.IO: open (握手)
        if eio_type == "0":
            try:
                handshake = json.loads(raw[1:])
                self._ping_interval = handshake.get("pingInterval", HEARTBEAT_INTERVAL) / 1000.0
                logger.info("Engine.IO 握手完成, ping_interval=%.1fs", self._ping_interval)
            except (json.JSONDecodeError, ValueError):
                logger.debug("EIO open: %s", raw)
            return

        # Engine.IO: close
        if eio_type == "1":
            logger.info("Engine.IO close 收到")
            self._connected = False
            return

        # Engine.IO: ping → 回复 pong
        if eio_type == "2":
            if self._ws:
                asyncio.create_task(self._safe_send("3"))
            return

        # Engine.IO: pong (无需处理)
        if eio_type == "3":
            logger.debug("pong ←")
            return

        # Engine.IO: message → 进一步解析 Socket.IO 子协议
        if eio_type == "4":
            self._handle_sio_message(raw[1:])
            return

        logger.debug("未知 EIO 帧类型: %s", raw[:100])

    def _handle_sio_message(self, payload: str) -> None:
        """解析 Socket.IO 子协议消息，派发事件。

        Socket.IO 类型:
          "0{...}"  → connect
          "1"       → disconnect
          "2[...]"  → event (emit)
          "3[...]"  → event with ack
        """
        if not payload:
            return

        sio_type = payload[0]
        sio_data = payload[1:]

        # SIO connect
        if sio_type == "0":
            logger.info("Socket.IO 已连接 (namespace)")
            # 连接成功后发送认证
            asyncio.create_task(self._send_auth())
            return

        # SIO disconnect
        if sio_type == "1":
            logger.info("Socket.IO 断开 (namespace)")
            return

        # SIO event (无 ack)
        if sio_type == "2":
            self._dispatch_event(sio_data)
            return

        # SIO event (有 ack)
        if sio_type == "3":
            self._dispatch_event(sio_data)
            return

        logger.debug("未知 SIO 类型: %s", payload[:100])

    async def _send_auth(self) -> None:
        """发送认证消息。

        ⚠️ 推测接口：格式为 42["identify", {uid, token}]。
        实际认证协议需通过浏览器 DevTools → Network → WS 抓包确认。
        """
        try:
            auth_payload = json.dumps([
                "identify",
                {
                    "uid": self.user_id or "",
                    "token": self.api_key,
                },
            ], ensure_ascii=False)
            frame = f"42{auth_payload}"
            await self._ws.send(frame)  # type: ignore[union-attr]
            logger.info("认证消息已发送 (identify)")
        except Exception as exc:
            logger.error("发送认证消息失败: %s", exc)

    def _dispatch_event(self, sio_data: str) -> None:
        """将 SIO 事件数据解析为 (event_name, data) 并通过回调派发。"""
        try:
            parsed = json.loads(sio_data)
        except json.JSONDecodeError:
            logger.warning("无法解析 SIO 事件: %s", sio_data[:200])
            return

        if isinstance(parsed, list) and len(parsed) >= 2:
            event_name = str(parsed[0])
            event_data = parsed[1] if isinstance(parsed[1], dict) else {}
        elif isinstance(parsed, dict):
            # 有时事件直接是 dict
            event_name = parsed.get("event", "unknown")
            event_data = parsed
        else:
            logger.debug("未识别的 SIO 数据格式: %s", str(parsed)[:200])
            return

        logger.debug("← [%s] %s", event_name, str(event_data)[:200])

        # 异步调用回调
        if self.on_message:
            try:
                result = self.on_message(event_name, event_data)
                # 如果回调是协程，创建 task
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception as exc:
                logger.error("on_message 回调异常: %s", exc)

    async def _safe_send(self, message: str) -> None:
        """安全发送消息，忽略连接已关闭的错误。"""
        try:
            if self._ws:
                await self._ws.send(message)
        except ConnectionClosed:
            pass
