# routes/prices.py
# 价格查询路由：单物品查询与批量查询

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from schemas import (
    BatchPriceRequest,
    BatchPriceResponse,
    PriceItem,
    SinglePriceResponse,
)
from services.price_fetcher import CS2PriceFetcher, PriceFetchError
from services.name_normalizer import normalize_name

logger = logging.getLogger("routes.prices")

router = APIRouter()


def get_price_fetcher(request: Request) -> CS2PriceFetcher:
    """从应用状态获取 CS2PriceFetcher 实例。"""
    fetcher: Optional[CS2PriceFetcher] = request.app.state.price_fetcher
    if fetcher is None:
        raise HTTPException(
            status_code=503,
            detail="价格服务未初始化，请确认 cs2.sh API Key 已配置",
        )
    return fetcher


def _normalize_items(items_str: str) -> list[str]:
    """将逗号分隔的物品字符串分割为标准化的物品名列表。"""
    raw = [s.strip() for s in items_str.split(",") if s.strip()]
    return [normalize_name(r) for r in raw]


def _build_response(raw: dict) -> dict[str, PriceItem]:
    """将 CS2PriceFetcher 返回的原始字典转为 PriceItem 模型字典。"""
    result: dict[str, PriceItem] = {}
    for name, info in raw.items():
        result[name] = PriceItem(
            ask=info.get("ask", 0.0),
            bid=info.get("bid", 0.0),
            ask_volume=info.get("ask_volume", 0),
            updated_at=info.get("updated_at"),
            error=info.get("error"),
        )
    return result


# ────────────────────────── GET /api/prices ──────────────────────────

@router.get("", response_model=SinglePriceResponse)
@router.get("/", include_in_schema=False, response_model=SinglePriceResponse)
async def get_single_price(
    items: str = Query(..., description="物品名称，多个用逗号分隔，如 AK-47|Redline(FT),AWP|Asiimov(FN)"),
    source: str = Query("buff", description="价格来源: buff / youpin / c5 / igxe"),
    request: Request = None,
) -> SinglePriceResponse:
    """查询单个或多个物品的实时价格。

    示例: GET /api/prices?items=AK-47|Redline(FT)&source=buff
    """
    fetcher = get_price_fetcher(request)
    item_list = _normalize_items(items)

    if not item_list:
        raise HTTPException(status_code=400, detail="items 参数不能为空")

    try:
        if len(item_list) == 1:
            price = await fetcher.get_price(item_list[0], source)
            return SinglePriceResponse(data=_build_response({item_list[0]: price}))
        else:
            results = await fetcher.batch_get_prices(item_list, source)
            return SinglePriceResponse(data=_build_response(results))
    except PriceFetchError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("价格查询异常")
        raise HTTPException(status_code=500, detail=f"价格查询失败: {exc}")


# ────────────────────────── POST /api/prices/batch ──────────────────────────

@router.post("/batch", response_model=BatchPriceResponse)
@router.post("/batch/", include_in_schema=False, response_model=BatchPriceResponse)
async def batch_get_prices(
    body: BatchPriceRequest,
    request: Request = None,
) -> BatchPriceResponse:
    """批量查询物品价格（最多 100 个）。

    请求体示例:
        {"items": ["AK-47 | Redline (FT)", "AWP | Asiimov (FN)"], "source": "buff"}
    """
    fetcher = get_price_fetcher(request)

    if not body.items:
        raise HTTPException(status_code=400, detail="items 数组不能为空")

    normalized = [normalize_name(name) for name in body.items]

    try:
        results = await fetcher.batch_get_prices(normalized, body.source)
        return BatchPriceResponse(data=_build_response(results))
    except PriceFetchError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("批量价格查询异常")
        raise HTTPException(status_code=500, detail=f"批量价格查询失败: {exc}")


# ────────────────────────── POST /api/prices/refresh ──────────────────────────

@router.post("/refresh", response_model=BatchPriceResponse)
@router.post("/refresh/", include_in_schema=False, response_model=BatchPriceResponse)
async def refresh_prices(
    body: BatchPriceRequest,
    request: Request = None,
) -> BatchPriceResponse:
    """强制刷新指定物品价格（绕过缓存，直接调 API）。

    请求体示例:
        {"items": ["AK-47 | Redline (FT)"], "source": "buff"}
    """
    fetcher = get_price_fetcher(request)

    if not body.items:
        raise HTTPException(status_code=400, detail="items 数组不能为空")

    normalized = [normalize_name(name) for name in body.items]

    try:
        results = await fetcher.refresh_cache(normalized, body.source)
        return BatchPriceResponse(data=_build_response(results))
    except PriceFetchError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("刷新价格缓存异常")
        raise HTTPException(status_code=500, detail=f"刷新价格缓存失败: {exc}")
