# routes/stats.py
# 统计报表路由：市场/拍卖聚合统计

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import AuctionDeal, BidRecord, MarketDeal, PurchaseRecord

logger = logging.getLogger("routes.stats")

router = APIRouter()


# ────────────────────────── 模型 ──────────────────────────

class MarketStats(BaseModel):
    total_detected: int = 0
    total_bought: int = 0
    total_missed: int = 0
    win_rate: float = 0.0
    avg_discount_pct: float = 0.0
    total_spend_usd: float = 0.0
    total_saved_usd: float = 0.0


class AuctionStats(BaseModel):
    total_joined: int = 0
    total_won: int = 0
    total_aborted: int = 0
    total_expired: int = 0
    win_rate: float = 0.0
    avg_win_discount_pct: float = 0.0
    total_spend_usd: float = 0.0


class StatsResponse(BaseModel):
    period: str
    account_name: Optional[str] = None
    market: MarketStats
    auction: AuctionStats


# ────────────────────────── 辅助 ──────────────────────────

def _period_range(period: str) -> datetime:
    """计算时间段起始时间。"""
    now = datetime.now(timezone.utc)
    if period == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        return now - timedelta(days=7)
    elif period == "month":
        return now - timedelta(days=30)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


# ────────────────────────── GET /api/stats ──────────────────────────

@router.get("", response_model=StatsResponse)
@router.get("/", include_in_schema=False, response_model=StatsResponse)
async def get_stats(
    account_name: Optional[str] = Query(None, description="账号名，不传=所有"),
    period: str = Query("today", description="today / week / month"),
    db: AsyncSession = Depends(get_db),
) -> StatsResponse:
    """获取聚合统计报表。

    示例: GET /api/stats?account_name=主号&period=today
    """
    since = _period_range(period)

    # ── 市场统计 ──
    base_market = select(MarketDeal).where(MarketDeal.created_at >= since)
    if account_name:
        base_market = base_market.where(MarketDeal.account_name == account_name)

    # 总检测数
    total_detected = await _count(db, base_market)

    # 购买数 + 总花费
    bought_query = base_market.where(MarketDeal.status == "bought")
    total_bought = await _count(db, bought_query)

    spend_result = await db.execute(
        select(func.coalesce(func.sum(MarketDeal.empire_price_usd), 0.0))
        .where(MarketDeal.created_at >= since)
        .where(MarketDeal.status == "bought")
        .where(MarketDeal.account_name == account_name) if account_name
        else select(func.coalesce(func.sum(MarketDeal.empire_price_usd), 0.0))
        .where(MarketDeal.created_at >= since)
        .where(MarketDeal.status == "bought")
    )
    total_spend = float(spend_result.scalar() or 0)

    # 平均折扣（已购买的）
    avg_discount_result = await db.execute(
        select(func.coalesce(func.avg(MarketDeal.discount_pct), 0.0))
        .where(MarketDeal.created_at >= since)
        .where(MarketDeal.status == "bought")
    )
    avg_discount = float(avg_discount_result.scalar() or 0)

    # 漏掉数
    missed_query = base_market.where(MarketDeal.status.in_(["detected", "expired", "missed"]))
    total_missed = await _count(db, missed_query)

    # 节省金额（Buff价 - 实际支出）
    saved_result = await db.execute(
        select(func.coalesce(
            func.sum(MarketDeal.buff_price_usd - MarketDeal.empire_price_usd), 0.0
        ))
        .where(MarketDeal.created_at >= since)
        .where(MarketDeal.status == "bought")
    )
    total_saved = float(saved_result.scalar() or 0)

    market = MarketStats(
        total_detected=total_detected,
        total_bought=total_bought,
        total_missed=total_missed,
        win_rate=round(total_bought / total_detected * 100, 1) if total_detected > 0 else 0.0,
        avg_discount_pct=round(avg_discount, 2),
        total_spend_usd=round(total_spend, 2),
        total_saved_usd=round(total_saved, 2),
    )

    # ── 拍卖统计 ──
    base_auction = select(AuctionDeal).where(AuctionDeal.created_at >= since)
    if account_name:
        base_auction = base_auction.where(AuctionDeal.account_name == account_name)

    total_joined = await _count(db, base_auction)
    total_won = await _count(db, base_auction.where(AuctionDeal.won_by_me == True))
    total_aborted = await _count(db, base_auction.where(AuctionDeal.status == "aborted"))
    total_expired = await _count(db, base_auction.where(AuctionDeal.status == "expired"))

    # 中标平均折扣
    won_avg = await db.execute(
        select(func.coalesce(func.avg(AuctionDeal.max_discount_pct), 0.0))
        .where(AuctionDeal.won_by_me == True)
        .where(AuctionDeal.created_at >= since)
    )
    avg_win_discount = float(won_avg.scalar() or 0)

    # 中标总花费
    won_spend = await db.execute(
        select(func.coalesce(func.sum(AuctionDeal.final_bid), 0.0))
        .where(AuctionDeal.won_by_me == True)
        .where(AuctionDeal.created_at >= since)
    )
    total_auction_spend = float(won_spend.scalar() or 0)

    auction = AuctionStats(
        total_joined=total_joined,
        total_won=total_won,
        total_aborted=total_aborted,
        total_expired=total_expired,
        win_rate=round(total_won / total_joined * 100, 1) if total_joined > 0 else 0.0,
        avg_win_discount_pct=round(avg_win_discount, 2),
        total_spend_usd=round(total_auction_spend, 2),
    )

    return StatsResponse(
        period=period,
        account_name=account_name,
        market=market,
        auction=auction,
    )


async def _count(db: AsyncSession, query) -> int:
    """执行 COUNT 查询。"""
    # 重新构造 count 查询
    from sqlalchemy import literal_column
    count_q = select(func.count()).select_from(query.subquery())
    result = await db.execute(count_q)
    return result.scalar() or 0
