# routes/market.py
# 市场捡漏引擎控制路由：启停、记录查询、手动购买
#
# ⚠️ 注意：购买 API 基于推测，实际需根据抓包调整。

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal, get_db
from models import Account, MarketDeal, StrategyConfig
from schemas import (
    MarketDealItem,
    MarketDealsResponse,
    MarketStartResponse,
    MarketStatsResponse,
)
from security import SecureStorage
from services.empire_http import EmpireHTTPClient
from services.executor import TradeExecutor
from services.market_engine import MarketSnipeEngine
from services.price_fetcher import CS2PriceFetcher

logger = logging.getLogger("routes.market")

router = APIRouter()


# ────────────────────────── 路由本地模型 ──────────────────────────

class ManualBuyRequest(BaseModel):
    item_id: str


class ManualBuyResponse(BaseModel):
    status: str
    detail: Optional[str] = None


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
        raise HTTPException(status_code=400, detail="请先调用 /api/init")
    return storage


async def _get_account_info(
    account_name: str,
    storage: SecureStorage,
    db: AsyncSession,
) -> tuple[str, float]:
    """获取账号的 API Key 和 empire_rate。"""
    account_data = storage.get_account(account_name)
    if account_data is None:
        raise HTTPException(status_code=404, detail=f"账号 '{account_name}' 不存在")
    return account_data["api_key"], account_data["empire_rate"]


# ────────────────────────── POST /api/market/start ──────────────────────────

@router.post("/start", response_model=MarketStartResponse)
@router.post("/start/", include_in_schema=False, response_model=MarketStartResponse)
async def market_start(
    request: Request,
    storage: SecureStorage = Depends(get_storage),
    db: AsyncSession = Depends(get_db),
) -> MarketStartResponse:
    """启动市场捡漏引擎。

    使用当前激活账号的 API Key 连接 Empire，
    读取账号策略配置，启动后台轮询监控。
    """
    current = request.app.state.current_account
    if not current:
        raise HTTPException(status_code=400, detail="未切换账号，请先 POST /api/accounts/{name}/switch")

    # 如果已在运行，先停止
    existing: Optional[MarketSnipeEngine] = request.app.state.market_engine
    if existing and existing.is_running:
        await existing.stop_monitoring()

    api_key, empire_rate = await _get_account_info(current, storage, db)

    # 获取或创建价格服务
    price_fetcher: Optional[CS2PriceFetcher] = request.app.state.price_fetcher
    if price_fetcher is None:
        raise HTTPException(
            status_code=503,
            detail="价格服务未初始化，请先设置 cs2.sh API Key (POST /api/config)",
        )

    # 创建 HTTP 客户端
    http_client = EmpireHTTPClient(api_key=api_key, timeout=10.0)

    # 创建引擎
    engine = MarketSnipeEngine(
        http_client=http_client,
        price_fetcher=price_fetcher,
        session_factory=AsyncSessionLocal,
        account_name=current,
        empire_rate=empire_rate,
    )

    # 加载策略
    await engine.reload_strategy()

    # 启动
    await engine.start_monitoring()

    request.app.state.market_engine = engine
    # 同时保存 http_client 供余额查询等使用
    request.app.state.empire_http = http_client

    logger.info("市场捡漏引擎启动成功: account=%s", current)

    return MarketStartResponse(
        status="started",
        account=current,
        stats=engine.stats,
    )


# ────────────────────────── POST /api/market/stop ──────────────────────────

@router.post("/stop", response_model=MarketStatsResponse)
@router.post("/stop/", include_in_schema=False, response_model=MarketStatsResponse)
async def market_stop(request: Request) -> MarketStatsResponse:
    """停止市场捡漏引擎。"""
    engine: Optional[MarketSnipeEngine] = request.app.state.market_engine

    if engine is None or not engine.is_running:
        return MarketStatsResponse(status="not_running")

    stats = engine.stats
    await engine.stop_monitoring()
    request.app.state.market_engine = None

    return MarketStatsResponse(status="stopped", stats=stats)


# ────────────────────────── GET /api/market/deals ──────────────────────────

@router.get("/deals", response_model=MarketDealsResponse)
@router.get("/deals/", include_in_schema=False, response_model=MarketDealsResponse)
async def market_deals(
    status_filter: Optional[str] = Query(None, alias="status", description="detected / bought / failed"),
    limit: int = Query(50, ge=1, le=500, description="返回条数"),
    db: AsyncSession = Depends(get_db),
) -> MarketDealsResponse:
    """查询市场捡漏记录。

    - ?status=detected  → 仅未购买的漏单
    - ?status=bought    → 已购买的记录
    - 不传 status      → 全部
    """
    if status_filter:
        query = select(MarketDeal).where(
            MarketDeal.status == status_filter
        ).order_by(desc(MarketDeal.created_at)).limit(limit)
    else:
        query = select(MarketDeal).order_by(
            desc(MarketDeal.created_at)
        ).limit(limit)

    result = await db.execute(query)
    deals = result.scalars().all()

    # 总数
    count_query = select(func.count(MarketDeal.id))
    if status_filter:
        count_query = count_query.where(MarketDeal.status == status_filter)
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    return MarketDealsResponse(
        deals=[
            MarketDealItem(
                id=deal.id,
                item_id=deal.item_id,
                market_hash_name=deal.market_hash_name,
                wear=deal.wear,
                empire_price_usd=deal.empire_price_usd,
                buff_price_usd=deal.buff_price_usd,
                discount_pct=deal.discount_pct,
                status=deal.status,
                account_name=deal.account_name,
                created_at=deal.created_at.isoformat() if deal.created_at else None,
                bought_at=deal.bought_at.isoformat() if deal.bought_at else None,
            )
            for deal in deals
        ],
        total=total,
    )


# ────────────────────────── POST /api/market/buy ──────────────────────────

@router.post("/buy", response_model=ManualBuyResponse)
@router.post("/buy/", include_in_schema=False, response_model=ManualBuyResponse)
async def market_buy(
    body: ManualBuyRequest,
    request: Request,
) -> ManualBuyResponse:
    """手动购买指定物品（通过 TradeExecutor 执行）。

    ⚠️ 推测接口：实际购买 API 参数需根据抓包确认。
    """
    http_client: Optional[EmpireHTTPClient] = request.app.state.empire_http
    if http_client is None:
        raise HTTPException(status_code=400, detail="未连接 Empire，请先 POST /api/market/start")

    current = request.app.state.current_account or "unknown"
    if not body.item_id:
        raise HTTPException(status_code=400, detail="item_id 不能为空")

    # 使用 TradeExecutor 统一执行购买
    try:
        executor = _get_or_create_executor(request, http_client)
        result = await executor.buy_item(
            item_id=body.item_id,
            item_name=body.item_id,
            price_usd=0.0,
            account_name=current,
            is_auto=False,
        )

        if result["status"] == "success":
            return ManualBuyResponse(status="ok", detail=f"购买成功: {result}")
        else:
            raise HTTPException(status_code=400, detail=result.get("reason", "购买失败"))

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("手动购买失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"购买失败: {exc}")


# ────────────────────────── GET /api/market/status ──────────────────────────

@router.get("/status", response_model=MarketStatsResponse)
@router.get("/status/", include_in_schema=False, response_model=MarketStatsResponse)
async def market_status(request: Request) -> MarketStatsResponse:
    """获取捡漏引擎运行状态。"""
    engine: Optional[MarketSnipeEngine] = request.app.state.market_engine

    if engine is None:
        return MarketStatsResponse(status="not_started", account=request.app.state.current_account)

    return MarketStatsResponse(
        status="running" if engine.is_running else "stopped",
        account=engine.account_name,
        stats=engine.stats,
    )


# ────────────────────────── 内部辅助 ──────────────────────────

def _get_or_create_executor(request: Request, http_client: EmpireHTTPClient) -> TradeExecutor:
    """获取或创建 TradeExecutor 实例，缓存在 app.state 上。"""
    executor = getattr(request.app.state, "trade_executor", None)
    if executor is None:
        executor = TradeExecutor(
            http_client=http_client,
            session_factory=AsyncSessionLocal,
        )
        request.app.state.trade_executor = executor
    return executor
