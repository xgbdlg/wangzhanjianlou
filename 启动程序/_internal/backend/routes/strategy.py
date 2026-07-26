# routes/strategy.py
# 策略配置路由：读取与更新账号/全局策略

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Account, StrategyConfig
from schemas import StatusResponse, StrategyRequest, StrategyResponse

router = APIRouter()


@router.get("", response_model=StrategyResponse)
@router.get("/", include_in_schema=False, response_model=StrategyResponse)
async def get_strategy(
    account_name: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
) -> StrategyResponse:
    """读取账号专属策略或全局策略。"""
    if account_name:
        account_query = select(Account).where(Account.name == account_name)
        account_result = await db.execute(account_query)
        account = account_result.scalar_one_or_none()
        if account is None:
            raise HTTPException(status_code=404, detail="账号不存在")

        query = select(StrategyConfig).where(
            StrategyConfig.account_id == account.id,
            StrategyConfig.is_global == False,
        )
        result = await db.execute(query)
        strategy = result.scalars().first()
        if strategy:
            return StrategyResponse(
                buff_rate=strategy.buff_rate,
                min_deal_pct=strategy.min_deal_pct,
                max_loss_pct=strategy.max_loss_pct,
                auto_bid=strategy.auto_bid,
                auto_buy=strategy.auto_buy,
                max_bid_usd=strategy.max_bid_usd,
                max_buy_usd=strategy.max_buy_usd,
                min_item_price=strategy.min_item_price,
                max_item_price=strategy.max_item_price,
                whitelist=strategy.whitelist,
                blacklist=strategy.blacklist,
                wear_filter=strategy.wear_filter,
                bid_delay_ms=strategy.bid_delay_ms,
            )
    query = select(StrategyConfig).where(StrategyConfig.is_global == True)
    result = await db.execute(query)
    strategy = result.scalars().first()
    if strategy is None:
        raise HTTPException(status_code=404, detail="未找到全局策略配置")
    return StrategyResponse(
        buff_rate=strategy.buff_rate,
        min_deal_pct=strategy.min_deal_pct,
        max_loss_pct=strategy.max_loss_pct,
        auto_bid=strategy.auto_bid,
        auto_buy=strategy.auto_buy,
        max_bid_usd=strategy.max_bid_usd,
        max_buy_usd=strategy.max_buy_usd,
        min_item_price=strategy.min_item_price,
        max_item_price=strategy.max_item_price,
        whitelist=strategy.whitelist,
        blacklist=strategy.blacklist,
        wear_filter=strategy.wear_filter,
        bid_delay_ms=strategy.bid_delay_ms,
    )


@router.post("", response_model=StatusResponse)
@router.post("/", include_in_schema=False, response_model=StatusResponse)
async def save_strategy(
    body: StrategyRequest,
    db: AsyncSession = Depends(get_db),
) -> StatusResponse:
    """保存或更新账号专属策略，或更新全局策略。"""
    account_id = None
    if body.account_name:
        query = select(Account).where(Account.name == body.account_name)
        result = await db.execute(query)
        account = result.scalar_one_or_none()
        if account is None:
            raise HTTPException(status_code=404, detail="账号不存在")
        account_id = account.id

    if account_id is not None:
        query = select(StrategyConfig).where(
            StrategyConfig.account_id == account_id,
            StrategyConfig.is_global == False,
        )
    else:
        query = select(StrategyConfig).where(StrategyConfig.is_global == True)

    result = await db.execute(query)
    strategy = result.scalars().first()
    if strategy is None:
        strategy = StrategyConfig(
            account_id=account_id,
            is_global=(account_id is None),
        )

    strategy.buff_rate = body.buff_rate
    strategy.min_deal_pct = body.min_deal_pct
    strategy.max_loss_pct = body.max_loss_pct
    strategy.auto_bid = body.auto_bid
    strategy.auto_buy = body.auto_buy
    strategy.max_bid_usd = body.max_bid_usd
    strategy.max_buy_usd = body.max_buy_usd
    strategy.min_item_price = body.min_item_price
    strategy.max_item_price = body.max_item_price
    strategy.whitelist = body.whitelist
    strategy.blacklist = body.blacklist
    strategy.wear_filter = body.wear_filter
    strategy.bid_delay_ms = body.bid_delay_ms

    db.add(strategy)
    await db.commit()

    return {"status": "ok"}
