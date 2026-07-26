# routes/auction.py
# 拍卖控制路由：启停引擎、手动出价、放弃竞拍、历史查询

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal, get_db
from models import Account, AuctionDeal
from schemas import MarketStatsResponse
from security import SecureStorage
from services.auction_engine import AuctionSnipeEngine
from services.empire_http import EmpireHTTPClient
from services.executor import TradeExecutor
from services.price_fetcher import CS2PriceFetcher

logger = logging.getLogger("routes.auction")

router = APIRouter()


# ────────────────────────── 路由本地模型 ──────────────────────────

class AuctionItemResponse(BaseModel):
    id: str
    market_hash_name: str
    wear: Optional[str]
    base_price: float
    current_bid: float
    buff_price_usd: float
    discount_pct: float
    status: str
    account_name: str
    bid_count: int = 0


class ActiveAuctionsResponse(BaseModel):
    auctions: list[dict]
    total: int


class BidRequest(BaseModel):
    amount: Optional[float] = None  # 不传则自动计算 current_bid × 1.01


class SimpleResponse(BaseModel):
    status: str


class AuctionHistoryItem(BaseModel):
    id: str
    market_hash_name: str
    wear: Optional[str]
    base_price: float
    starting_bid: float
    final_bid: Optional[float]
    buff_price_usd: float
    max_discount_pct: float
    status: str
    won_by_me: bool
    account_name: str
    created_at: Optional[str]
    ended_at: Optional[str]


class AuctionHistoryResponse(BaseModel):
    auctions: list[AuctionHistoryItem]
    total: int


# ────────────────────────── 辅助依赖 ──────────────────────────

def get_storage(request: Request) -> SecureStorage:
    header_password = request.headers.get("X-Master-Password")
    storage = request.app.state.storage
    if header_password:
        storage = SecureStorage(master_password=header_password)
        storage.init_storage()
        request.app.state.storage = storage
    if storage is None:
        raise HTTPException(status_code=400, detail="请先调用 /api/init")
    return storage


async def _get_account_info(account_name: str, storage: SecureStorage) -> tuple[str, float]:
    account_data = storage.get_account(account_name)
    if account_data is None:
        raise HTTPException(status_code=404, detail=f"账号 '{account_name}' 不存在")
    return account_data["api_key"], account_data["empire_rate"]


# ────────────────────────── POST /api/auction/start ──────────────────────────

@router.post("/start", response_model=MarketStatsResponse)
@router.post("/start/", include_in_schema=False, response_model=MarketStatsResponse)
async def auction_start(
    request: Request,
    storage: SecureStorage = Depends(get_storage),
) -> MarketStatsResponse:
    """启动拍卖捡漏引擎。

    注册 WS 事件处理器，开始监听拍卖事件。
    需要: 已连接 Empire WebSocket (POST /api/empire/connect)
    """
    current = request.app.state.current_account
    if not current:
        raise HTTPException(status_code=400, detail="未切换账号")

    # 先停旧引擎
    existing: Optional[AuctionSnipeEngine] = request.app.state.auction_engine
    if existing and existing.is_running:
        await existing.stop()

    api_key, empire_rate = await _get_account_info(current, storage)

    price_fetcher: Optional[CS2PriceFetcher] = request.app.state.price_fetcher
    if price_fetcher is None:
        raise HTTPException(status_code=503, detail="价格服务未初始化")

    http_client = EmpireHTTPClient(api_key=api_key, timeout=10.0)

    engine = AuctionSnipeEngine(
        http_client=http_client,
        price_fetcher=price_fetcher,
        session_factory=AsyncSessionLocal,
        account_name=current,
        empire_rate=empire_rate,
    )

    # 将引擎注入 WS 客户端的事件回调链
    engine.set_notify_callback(_make_frontend_notifier(request))

    await engine.start()

    request.app.state.auction_engine = engine
    # 如果 WS 已连接，注册事件分发
    ws_client = request.app.state.empire_ws
    if ws_client:
        _inject_ws_dispatcher(ws_client, engine)

    logger.info("拍卖引擎启动: account=%s", current)

    return MarketStatsResponse(status="started", account=current, stats=engine.stats)


# ────────────────────────── POST /api/auction/stop ──────────────────────────

@router.post("/stop", response_model=MarketStatsResponse)
@router.post("/stop/", include_in_schema=False, response_model=MarketStatsResponse)
async def auction_stop(request: Request) -> MarketStatsResponse:
    """停止拍卖引擎。"""
    engine: Optional[AuctionSnipeEngine] = request.app.state.auction_engine
    if engine is None or not engine.is_running:
        return MarketStatsResponse(status="not_running")

    stats = engine.stats
    await engine.stop()
    request.app.state.auction_engine = None
    return MarketStatsResponse(status="stopped", stats=stats)


# ────────────────────────── GET /api/auction/active ──────────────────────────

@router.get("/active", response_model=ActiveAuctionsResponse)
@router.get("/active/", include_in_schema=False, response_model=ActiveAuctionsResponse)
async def auction_active(request: Request) -> ActiveAuctionsResponse:
    """获取当前活跃拍卖列表。"""
    engine: Optional[AuctionSnipeEngine] = request.app.state.auction_engine
    if engine is None:
        return ActiveAuctionsResponse(auctions=[], total=0)

    items = [
        item.to_dict()
        for item in engine.active_auctions.values()
        if item.status.value not in ("expired",)
    ]
    return ActiveAuctionsResponse(auctions=items, total=len(items))


# ────────────────────────── POST /api/auction/{id}/bid ──────────────────────────

@router.post("/{auction_id}/bid", response_model=SimpleResponse)
@router.post("/{auction_id}/bid/", include_in_schema=False, response_model=SimpleResponse)
async def auction_bid(
    auction_id: str,
    body: BidRequest,
    request: Request,
) -> SimpleResponse:
    """手动出价（通过 TradeExecutor 执行余额检查 + 出价 + 记录）。

    body: {"amount": 1250.0}  -- 不传则自动计算 current_bid × 1.01
    """
    engine: Optional[AuctionSnipeEngine] = request.app.state.auction_engine
    if engine is None:
        raise HTTPException(status_code=400, detail="拍卖引擎未启动")

    if auction_id not in engine.active_auctions:
        raise HTTPException(status_code=404, detail="拍卖未找到")

    auction = engine.active_auctions[auction_id]
    current = request.app.state.current_account or "unknown"

    # 计算出价金额
    bid_amount = body.amount
    if bid_amount is None:
        bid_amount = auction.current_bid * 1.01  # 自动加 1%

    discount = auction.calculate_discount(engine.empire_rate)

    # 通过 TradeExecutor 执行出价
    http_client: Optional[EmpireHTTPClient] = request.app.state.empire_http
    if http_client is None:
        raise HTTPException(status_code=400, detail="未连接 Empire")

    executor = _get_or_create_auction_executor(request, http_client)
    result = await executor.place_bid(
        auction_id=auction_id,
        amount=bid_amount,
        auction_name=auction.market_hash_name,
        discount_at_bid=discount,
        account_name=current,
        is_auto=False,
    )

    if result["status"] != "success":
        raise HTTPException(status_code=400, detail=result.get("reason", "出价失败"))

    # 更新引擎内部状态
    auction.current_bid = bid_amount
    auction.bid_count += 1

    return SimpleResponse(status="ok")


# ────────────────────────── POST /api/auction/{id}/abort ──────────────────────────

@router.post("/{auction_id}/abort", response_model=SimpleResponse)
@router.post("/{auction_id}/abort/", include_in_schema=False, response_model=SimpleResponse)
async def auction_abort(auction_id: str, request: Request) -> SimpleResponse:
    """手动放弃竞拍。"""
    engine: Optional[AuctionSnipeEngine] = request.app.state.auction_engine
    if engine is None:
        raise HTTPException(status_code=400, detail="拍卖引擎未启动")

    success = await engine.manual_abort(auction_id)
    if not success:
        raise HTTPException(status_code=404, detail="拍卖未找到或已结束")

    return SimpleResponse(status="ok")


# ────────────────────────── GET /api/auction/history ──────────────────────────

@router.get("/history", response_model=AuctionHistoryResponse)
@router.get("/history/", include_in_schema=False, response_model=AuctionHistoryResponse)
async def auction_history(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> AuctionHistoryResponse:
    """查询拍卖历史记录。"""
    if status_filter:
        query = select(AuctionDeal).where(
            AuctionDeal.status == status_filter
        ).order_by(desc(AuctionDeal.created_at)).limit(limit)
    else:
        query = select(AuctionDeal).order_by(desc(AuctionDeal.created_at)).limit(limit)

    result = await db.execute(query)
    deals = result.scalars().all()

    count_q = select(func.count(AuctionDeal.id))
    if status_filter:
        count_q = count_q.where(AuctionDeal.status == status_filter)
    count_result = await db.execute(count_q)
    total = count_result.scalar() or 0

    return AuctionHistoryResponse(
        auctions=[
            AuctionHistoryItem(
                id=d.id,
                market_hash_name=d.market_hash_name,
                wear=d.wear,
                base_price=d.base_price,
                starting_bid=d.starting_bid,
                final_bid=d.final_bid,
                buff_price_usd=d.buff_price_usd,
                max_discount_pct=d.max_discount_pct,
                status=d.status,
                won_by_me=d.won_by_me,
                account_name=d.account_name,
                created_at=d.created_at.isoformat() if d.created_at else None,
                ended_at=d.ended_at.isoformat() if d.ended_at else None,
            )
            for d in deals
        ],
        total=total,
    )


# ────────────────────────── GET /api/auction/status ──────────────────────────

@router.get("/status", response_model=MarketStatsResponse)
@router.get("/status/", include_in_schema=False, response_model=MarketStatsResponse)
async def auction_status(request: Request) -> MarketStatsResponse:
    """获取拍卖引擎状态。"""
    engine: Optional[AuctionSnipeEngine] = request.app.state.auction_engine
    if engine is None:
        return MarketStatsResponse(status="not_started", account=request.app.state.current_account)

    return MarketStatsResponse(
        status="running" if engine.is_running else "stopped",
        account=engine.account_name,
        stats=engine.stats,
    )


# ────────────────────────── WS 事件分发绑定 ──────────────────────────

def _inject_ws_dispatcher(ws_client, engine: AuctionSnipeEngine) -> None:
    """将拍卖引擎的事件处理注入 WS 客户端的消息回调链。

    在原有 on_message 基础上包装一层：拍卖事件 → engine.handle_event()
    市场事件预留 market_engine 分发。
    """
    original_callback = ws_client.on_message

    from empire_config import EMPIRE_WS as WSCFG
    EV = WSCFG["events"]

    async def dispatch_wrapper(event_name: str, data: dict) -> None:
        # 拍卖相关事件 → 拍卖引擎
        auction_events = [EV[k] for k in EV if k.startswith("auction_")]
        if event_name in auction_events:
            await engine.handle_event({"event": event_name, "data": data})

        # 继续调用原有回调（写入 last_event 等）
        if original_callback:
            try:
                result = original_callback(event_name, data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

    ws_client.on_message = dispatch_wrapper


def _make_frontend_notifier(request: Request):
    """创建前端通知回调。"""

    async def notifier(msg: dict) -> None:
        request.app.state.empire_last_event = {
            "event": "auction_update",
            "data": msg,
        }

    return notifier


def _get_or_create_auction_executor(request: Request, http_client: EmpireHTTPClient) -> TradeExecutor:
    """获取或创建 TradeExecutor 实例。"""
    executor = getattr(request.app.state, "trade_executor", None)
    if executor is None:
        executor = TradeExecutor(
            http_client=http_client,
            session_factory=AsyncSessionLocal,
        )
        request.app.state.trade_executor = executor
    return executor
