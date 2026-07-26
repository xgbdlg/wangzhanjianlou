# services/price_fetcher.py
# CS2.SH 价格获取模块，实现 L1(内存) → L2(SQLite) → L3(API) 三级缓存

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio.session import async_sessionmaker

from models import PriceCache

logger = logging.getLogger("price_fetcher")

# 缓存 TTL 常量
L1_TTL_SECONDS = 300       # 内存缓存 5 分钟
L2_TTL_SECONDS = 900       # SQLite 缓存 15 分钟

# API 配置
CS2SH_BASE_URL = "https://api.cs2.sh"
CS2SH_SINGLE_URL = f"{CS2SH_BASE_URL}/v1/prices/latest"
CS2SH_BATCH_URL = f"{CS2SH_BASE_URL}/v1/prices/latest"
CS2SH_MAX_BATCH_ITEMS = 100

# HTTP 超时
REQUEST_TIMEOUT = 15.0


class PriceFetchError(Exception):
    """价格获取异常，包含友好错误信息。"""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class CS2PriceFetcher:
    """CS2.SH 异步价格查询器，带三级缓存。

    使用方式:
        from database import AsyncSessionLocal
        fetcher = CS2PriceFetcher(api_key="xxx", session_factory=AsyncSessionLocal)
        price = await fetcher.get_price("AK-47 | Redline (Field-Tested)")
    """

    def __init__(self, api_key: str, session_factory: async_sessionmaker):
        self.api_key = api_key
        self.session_factory = session_factory
        self.base_url = CS2SH_BASE_URL
        self._memory_cache: dict[str, dict] = {}
        self._memory_cache_lock = asyncio.Lock()

    # ────────────────────────── 公开方法 ──────────────────────────

    async def get_price(self, market_hash_name: str, source: str = "buff") -> dict:
        """获取单个物品的实时价格。

        缓存查找顺序: L1 内存 → L2 SQLite → L3 API

        Returns:
            {"ask": float, "bid": float, "ask_volume": int, "updated_at": str}
        """
        cache_key = self._cache_key(market_hash_name, source)

        # L1: 内存缓存
        cached = await self._check_l1(cache_key)
        if cached:
            logger.debug("L1 hit: %s", cache_key)
            return cached

        # L2: SQLite 缓存
        cached = await self._check_l2(market_hash_name, source)
        if cached:
            logger.debug("L2 hit: %s", cache_key)
            await self._write_l1(cache_key, cached)
            return cached

        # L3: API 请求
        logger.debug("L3 fetch: %s", cache_key)
        data = await self._fetch_from_api(market_hash_name, source)
        await self._write_l1(cache_key, data)
        await self._write_l2(market_hash_name, source, data)
        return data

    async def batch_get_prices(self, items: list[str], source: str = "buff") -> dict:
        """批量获取物品价格（最多 100 个）。

        Returns:
            {"item_name": {"ask": ..., "bid": ..., "ask_volume": ..., "updated_at": ...}, ...}
        """
        if not items:
            return {}

        if len(items) > CS2SH_MAX_BATCH_ITEMS:
            items = items[:CS2SH_MAX_BATCH_ITEMS]
            logger.warning("批量查询被截断至 %d 个物品", CS2SH_MAX_BATCH_ITEMS)

        results: dict[str, dict] = {}
        uncached_items: list[str] = []

        # L1 + L2 检查
        for name in items:
            cache_key = self._cache_key(name, source)

            cached = await self._check_l1(cache_key)
            if cached:
                results[name] = cached
                continue

            cached = await self._check_l2(name, source)
            if cached:
                results[name] = cached
                await self._write_l1(cache_key, cached)
                continue

            uncached_items.append(name)

        # L3: 批量未命中一起查询
        if uncached_items:
            batch_data = await self._batch_fetch_from_api(uncached_items, source)
            for name in uncached_items:
                if name in batch_data:
                    data = batch_data[name]
                    cache_key = self._cache_key(name, source)
                    results[name] = data
                    await self._write_l1(cache_key, data)
                    await self._write_l2(name, source, data)
                else:
                    results[name] = {
                        "ask": 0.0,
                        "bid": 0.0,
                        "ask_volume": 0,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "error": "物品在 cs2.sh 上未找到",
                    }

        return results

    async def refresh_cache(self, items: list[str], source: str = "buff") -> dict:
        """强制刷新指定物品价格（绕过 L1/L2 缓存，直接调用 API 并更新缓存）。"""
        if not items:
            return {}

        if len(items) > CS2SH_MAX_BATCH_ITEMS:
            items = items[:CS2SH_MAX_BATCH_ITEMS]

        batch_data = await self._batch_fetch_from_api(items, source)
        results: dict[str, dict] = {}

        for name in items:
            if name in batch_data:
                data = batch_data[name]
            else:
                data = {
                    "ask": 0.0,
                    "bid": 0.0,
                    "ask_volume": 0,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "error": "物品在 cs2.sh 上未找到",
                }

            cache_key = self._cache_key(name, source)
            results[name] = data
            await self._write_l1(cache_key, data)
            await self._write_l2(name, source, data)

        return results

    async def cleanup_memory_cache(self) -> int:
        """清理过期的 L1 内存缓存条目。返回清理数量。"""
        now = datetime.now(timezone.utc)
        removed = 0
        async with self._memory_cache_lock:
            expired_keys = [
                key
                for key, entry in self._memory_cache.items()
                if now - entry["timestamp"] > timedelta(seconds=L1_TTL_SECONDS)
            ]
            for key in expired_keys:
                del self._memory_cache[key]
                removed += 1
        if removed:
            logger.info("L1 缓存清理: 移除 %d 条过期记录", removed)
        return removed

    async def cleanup_sqlite_cache(self) -> int:
        """清理过期的 L2 SQLite 缓存（cached_at 超过 L2_TTL 的行）。返回清理数量。"""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=L2_TTL_SECONDS)
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    delete(PriceCache).where(PriceCache.cached_at < cutoff)
                )
                await session.commit()
                removed = result.rowcount
            if removed:
                logger.info("L2 SQLite 缓存清理: 移除 %d 条过期记录", removed)
            return removed
        except Exception as exc:
            logger.error("L2 缓存清理失败: %s", exc)
            return 0

    # ────────────────────────── 内部方法 ──────────────────────────

    @staticmethod
    def _cache_key(market_hash_name: str, source: str) -> str:
        return f"{source}:{market_hash_name}"

    async def _check_l1(self, cache_key: str) -> Optional[dict]:
        """检查 L1 内存缓存，过期返回 None。"""
        async with self._memory_cache_lock:
            entry = self._memory_cache.get(cache_key)
        if entry:
            age = (datetime.now(timezone.utc) - entry["timestamp"]).total_seconds()
            if age < L1_TTL_SECONDS:
                return entry["data"]
        return None

    async def _check_l2(self, market_hash_name: str, source: str) -> Optional[dict]:
        """检查 L2 SQLite 缓存，过期返回 None。"""
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(PriceCache).where(
                        PriceCache.market_hash_name == market_hash_name,
                        PriceCache.source == source,
                    )
                )
                row = result.scalar_one_or_none()
                if row and row.cached_at:
                    age = (
                        datetime.now(timezone.utc)
                        - row.cached_at.replace(tzinfo=timezone.utc)
                    ).total_seconds()
                    if age < L2_TTL_SECONDS:
                        return {
                            "ask": row.buff_ask or 0.0,
                            "bid": row.buff_bid or 0.0,
                            "ask_volume": 0,
                            "updated_at": row.cached_at.isoformat(),
                        }
        except Exception as exc:
            logger.warning("L2 缓存查询异常: %s", exc)
        return None

    async def _write_l1(self, cache_key: str, data: dict) -> None:
        """写入 L1 内存缓存。"""
        async with self._memory_cache_lock:
            self._memory_cache[cache_key] = {
                "data": data.copy(),
                "timestamp": datetime.now(timezone.utc),
            }

    async def _write_l2(self, market_hash_name: str, source: str, data: dict) -> None:
        """写入 L2 SQLite 缓存（upsert）。"""
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(PriceCache).where(
                        PriceCache.market_hash_name == market_hash_name
                    )
                )
                row = result.scalar_one_or_none()
                if row:
                    row.buff_ask = data.get("ask")
                    row.buff_bid = data.get("bid")
                    row.source = source
                    row.cached_at = datetime.now(timezone.utc)
                else:
                    row = PriceCache(
                        market_hash_name=market_hash_name,
                        buff_ask=data.get("ask"),
                        buff_bid=data.get("bid"),
                        source=source,
                    )
                session.add(row)
                await session.commit()
        except Exception as exc:
            logger.warning("L2 缓存写入异常: %s", exc)

    async def _fetch_from_api(self, market_hash_name: str, source: str) -> dict:
        """L3: 单个物品 API 查询 (GET)。"""
        url = CS2SH_SINGLE_URL
        params = {"items": market_hash_name, "source": source}
        headers = self._auth_headers()

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return self._parse_single_response(response.json(), market_hash_name, source)
        except httpx.TimeoutException:
            raise PriceFetchError(f"请求 cs2.sh 超时 ({REQUEST_TIMEOUT}s)", status_code=408)
        except httpx.HTTPStatusError as exc:
            raise self._handle_http_error(exc)

    async def _batch_fetch_from_api(self, items: list[str], source: str) -> dict:
        """L3: 批量 API 查询 (POST)。"""
        url = CS2SH_BATCH_URL
        headers = self._auth_headers()
        body = {"items": items, "source": source}

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.post(url, json=body, headers=headers)
                response.raise_for_status()
                return self._parse_batch_response(response.json(), source)
        except httpx.TimeoutException:
            raise PriceFetchError(f"批量请求 cs2.sh 超时 ({REQUEST_TIMEOUT}s)", status_code=408)
        except httpx.HTTPStatusError as exc:
            raise self._handle_http_error(exc)

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    # ────────────────────────── 响应解析 ──────────────────────────

    def _parse_single_response(self, resp_data: dict, name: str, source: str) -> dict:
        """解析单物品 API 响应，提取 ask/bid。"""
        now_ts = datetime.now(timezone.utc).isoformat()

        try:
            item_data = resp_data.get("data", {}).get(name, {}).get(source, {})
            if item_data:
                return {
                    "ask": float(item_data.get("ask", 0)),
                    "bid": float(item_data.get("bid", 0)),
                    "ask_volume": int(item_data.get("ask_volume", 0)),
                    "updated_at": item_data.get("updated_at", now_ts),
                }
        except (TypeError, KeyError, ValueError) as exc:
            logger.warning("单物品响应解析异常: %s", exc)

        return {
            "ask": 0.0,
            "bid": 0.0,
            "ask_volume": 0,
            "updated_at": now_ts,
            "error": "响应解析失败",
        }

    def _parse_batch_response(self, resp_data: dict, source: str) -> dict:
        """解析批量 API 响应，返回 {name: {ask, bid, ...}}。"""
        now_ts = datetime.now(timezone.utc).isoformat()
        results: dict[str, dict] = {}

        data = resp_data.get("data", {})
        if not isinstance(data, dict):
            return results

        for name, sources in data.items():
            source_data = sources.get(source, {}) if isinstance(sources, dict) else {}
            if source_data:
                results[name] = {
                    "ask": float(source_data.get("ask", 0)),
                    "bid": float(source_data.get("bid", 0)),
                    "ask_volume": int(source_data.get("ask_volume", 0)),
                    "updated_at": source_data.get("updated_at", now_ts),
                }
            else:
                results[name] = {
                    "ask": 0.0,
                    "bid": 0.0,
                    "ask_volume": 0,
                    "updated_at": now_ts,
                }
        return results

    # ────────────────────────── 错误处理 ──────────────────────────

    @staticmethod
    def _handle_http_error(exc: httpx.HTTPStatusError) -> PriceFetchError:
        """将 HTTP 错误转为友好消息。"""
        status = exc.response.status_code

        messages = {
            401: "cs2.sh API Key 无效 (401)，请检查配置",
            403: "cs2.sh 拒绝访问 (403)，请检查 API Key 权限",
            404: "cs2.sh 接口未找到 (404)，请检查 API 地址",
            429: "cs2.sh 请求过于频繁 (429)，请稍后重试",
        }

        if status in messages:
            return PriceFetchError(messages[status], status_code=status)
        if 500 <= status < 600:
            return PriceFetchError(f"cs2.sh 服务器错误 ({status})，请稍后重试", status_code=status)
        return PriceFetchError(f"cs2.sh API 请求失败 ({status}): {exc}", status_code=status)
