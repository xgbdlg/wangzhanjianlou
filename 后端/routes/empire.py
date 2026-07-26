# routes/empire.py
# Empire 连接管理路由：连接、余额查询、断开
#
# ⚠️ 注意：部分接口基于推测，标注了"推测接口"，实际需根据抓包调整。

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from schemas import EmpireBalanceResponse, EmpireConnectResponse, EmpireStatusResponse
from security import SecureStorage
from services.empire_http import EmpireHTTPClient, EmpireHTTPError
from services.empire_ws import EmpireWebSocketClient

logger = logging.getLogger("routes.empire")

router = APIRouter()


# ────────────────────────── 辅助依赖 ──────────────────────────

def get_storage(request: Request) -> SecureStorage:
    """获取 SecureStorage 实例。"""
    header_password = request.headers.get("X-Master-Password")
    storage = request.app.state.storage
    if header_password:
        storage = SecureStorage(master_password=header_password)
        storage.init_storage()
        request.app.state.storage = storage
    if storage is None:
        raise HTTPException(
            status_code=400,
            detail="Secure storage 未初始化，请先调用 /api/init 或传入 X-Master-Password",
        )
    return storage


def _get_account_api_key(
    account_name: str, storage: SecureStorage
) -> str:
    """从 SecureStorage 中获取指定账号的解密 API Key。"""
    account_data = storage.get_account(account_name)
    if account_data is None:
        raise HTTPException(status_code=404, detail=f"账号 '{account_name}' 不存在")
    return account_data["api_key"]


# ────────────────────────── POST /api/empire/connect ──────────────────────────

@router.post("/connect", response_model=EmpireConnectResponse)
@router.post("/connect/", include_in_schema=False, response_model=EmpireConnectResponse)
async def empire_connect(
    request: Request,
    storage: SecureStorage = Depends(get_storage),
) -> EmpireConnectResponse:
    """使用当前激活账号连接 Empire。

    1. 从 SecureStorage 获取当前账号 API Key
    2. 创建 HTTP 客户端并查询余额
    3. 创建 WebSocket 客户端并连接
    4. 存入 app.state 供后续使用

    需要先: POST /api/init 并 POST /api/accounts/{name}/switch
    """
    current = request.app.state.current_account
    if not current:
        raise HTTPException(status_code=400, detail="未切换账号，请先 POST /api/accounts/{name}/switch")

    api_key = _get_account_api_key(current, storage)

    # 关闭旧连接
    await _disconnect_existing(request)

    # 创建 HTTP 客户端并测试连接（15s 超时）
    http_client = EmpireHTTPClient(api_key=api_key, timeout=10.0)
    balance = None
    warning = None

    try:
        balance = await asyncio.wait_for(http_client.get_balance(), timeout=12.0)
        logger.info("Empire HTTP 连接成功, 账号=%s, 余额=%s", current, balance)
    except asyncio.TimeoutError:
        logger.warning("HTTP 余额查询超时 (%s)", current)
        warning = "余额查询超时（推测接口，请确认 API Key 是否有效）"
    except EmpireHTTPError as exc:
        logger.warning("获取余额失败 (%s): %s", current, exc)
        warning = f"余额获取失败: {exc}（推测接口）"
    except Exception as exc:
        logger.warning("HTTP 连接失败 (%s): %s", current, exc)
        warning = f"连接测试失败: {exc}（推测接口）"

    request.app.state.empire_http = http_client

    # 创建 WebSocket 客户端（10s 连接超时）
    ws_connected = False
    ws_client = EmpireWebSocketClient(
        api_key=api_key,
        on_message=_make_ws_callback(request),
    )

    try:
        await asyncio.wait_for(ws_client.connect(), timeout=10.0)
        ws_connected = True
        logger.info("Empire WebSocket 连接成功, 账号=%s", current)
    except asyncio.TimeoutError:
        logger.warning("WebSocket 连接超时 (%s)", current)
        ws_warn = "WS 连接超时（推测接口，请确认 WebSocket 地址）"
        warning = f"{warning}; {ws_warn}" if warning else ws_warn
    except Exception as exc:
        logger.warning("WebSocket 连接失败 (%s): %s", current, exc)
        ws_warn = f"WS 连接失败: {exc}（推测接口）"
        warning = f"{warning}; {ws_warn}" if warning else ws_warn

    request.app.state.empire_ws = ws_client

    return EmpireConnectResponse(
        status="connected",
        account=current,
        balance=balance,
        ws_connected=ws_connected,
        warning=warning,
    )


# ────────────────────────── GET /api/empire/balance ──────────────────────────

@router.get("/balance", response_model=EmpireBalanceResponse)
@router.get("/balance/", include_in_schema=False, response_model=EmpireBalanceResponse)
async def empire_balance(request: Request) -> EmpireBalanceResponse:
    """查询当前账号的 Empire 余额。"""
    current = request.app.state.current_account
    if not current:
        raise HTTPException(status_code=400, detail="未切换账号")

    http_client: Optional[EmpireHTTPClient] = request.app.state.empire_http
    if http_client is None:
        raise HTTPException(status_code=400, detail="未连接 Empire，请先 POST /api/empire/connect")

    try:
        balance = await http_client.get_balance()
        return EmpireBalanceResponse(account=current, balance=balance)
    except EmpireHTTPError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"余额查询失败: {exc}")


# ────────────────────────── POST /api/empire/disconnect ──────────────────────────

@router.post("/disconnect", response_model=EmpireStatusResponse)
@router.post("/disconnect/", include_in_schema=False, response_model=EmpireStatusResponse)
async def empire_disconnect(request: Request) -> EmpireStatusResponse:
    """断开 Empire 连接。"""
    await _disconnect_existing(request)
    return EmpireStatusResponse(status="disconnected")


# ────────────────────────── GET /api/empire/status ──────────────────────────

@router.get("/status", response_model=EmpireStatusResponse)
@router.get("/status/", include_in_schema=False, response_model=EmpireStatusResponse)
async def empire_status(request: Request) -> EmpireStatusResponse:
    """获取当前 Empire 连接状态。"""
    http_client: Optional[EmpireHTTPClient] = request.app.state.empire_http
    ws_client: Optional[EmpireWebSocketClient] = request.app.state.empire_ws

    return EmpireStatusResponse(
        status="connected" if (http_client and ws_client and ws_client.is_connected) else "disconnected",
        account=request.app.state.current_account,
        http_connected=http_client is not None,
        ws_connected=ws_client is not None and ws_client.is_connected,
    )


# ────────────────────────── 内部辅助 ──────────────────────────

async def _disconnect_existing(request: Request) -> None:
    """关闭已存在的 HTTP 和 WS 连接。"""
    ws_client: Optional[EmpireWebSocketClient] = request.app.state.empire_ws
    if ws_client:
        try:
            await ws_client.disconnect()
        except Exception as exc:
            logger.warning("关闭 WS 连接异常: %s", exc)
        request.app.state.empire_ws = None

    http_client: Optional[EmpireHTTPClient] = request.app.state.empire_http
    if http_client:
        try:
            await http_client.close()
        except Exception as exc:
            logger.warning("关闭 HTTP 连接异常: %s", exc)
        request.app.state.empire_http = None


def _make_ws_callback(request: Request):
    """创建 WS 消息回调函数（闭包捕获 request 以访问 app.state）。

    ⚠️ 推测接口：事件名称基于常见 CSGOEmpire WebSocket 事件推断。
    实际事件名需通过浏览器 DevTools → Network → WS → Messages 抓包确认。
    """
    async def on_empire_event(event: str, data: dict) -> None:
        logger.info("Empire WS 事件: [%s] %s", event, str(data)[:300])

        # 将事件存入最新消息（供前端轮询）
        if not hasattr(request.app.state, "empire_last_event"):
            request.app.state.empire_last_event = None
        request.app.state.empire_last_event = {
            "event": event,
            "data": data,
        }

    return on_empire_event
