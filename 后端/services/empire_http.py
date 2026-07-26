# services/empire_http.py
# CSGOEmpire HTTP API 客户端，带令牌桶限流和指数退避重试
#
# ⚠️ 接口路径和字段名配置在 后端/empire_config.py，抓包后修改该文件即可

import asyncio
import logging
import time
from typing import Any, Optional

from empire_config import ACTIVE_STATUSES, EMPIRE_HTTP as CFG

import httpx

logger = logging.getLogger("empire_http")

# 默认配置
DEFAULT_BASE_URL = "https://csgoempire.com"
RATE_LIMIT_REQUESTS = 120       # 每窗口最多 120 次请求
RATE_LIMIT_WINDOW = 60.0        # 窗口 60 秒
MAX_RETRIES = 3                 # 最大重试次数
RETRY_BACKOFF_BASE = 1.0        # 退避基数 (1s, 2s, 4s)
RETRYABLE_STATUSES = {429, 500, 502, 503}


class RateLimiter:
    """令牌桶算法限流器。

    每 RATE_LIMIT_WINDOW 秒补充 RATE_LIMIT_REQUESTS 个令牌，
    请求前需获取令牌，无令牌时等待。
    """

    def __init__(self, max_tokens: int = RATE_LIMIT_REQUESTS, window: float = RATE_LIMIT_WINDOW):
        self.max_tokens = max_tokens
        self.window = window
        self._tokens = float(max_tokens)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """获取一个令牌，若不可用则等待。"""
        async with self._lock:
            self._refill()
            if self._tokens < 1.0:
                wait_time = (1.0 - self._tokens) * (self.window / self.max_tokens)
                logger.debug("令牌桶为空，等待 %.2fs", wait_time)
                await asyncio.sleep(wait_time)
                self._refill()
            self._tokens -= 1.0

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        new_tokens = elapsed * (self.max_tokens / self.window)
        self._tokens = min(self.max_tokens, self._tokens + new_tokens)
        self._last_refill = now


class EmpireHTTPError(Exception):
    """Empire HTTP 请求异常。"""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class EmpireHTTPClient:
    """CSGOEmpire HTTP API 客户端。

    使用方式:
        client = EmpireHTTPClient(api_key="...")
        items = await client.get_items()
        balance = await client.get_balance()
        await client.close()
    """

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 15.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.rate_limiter = RateLimiter()
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        """延迟创建 HTTP 客户端（复用连接池）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._auth_headers(),
                timeout=self._timeout,
            )
        return self._client

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ────────────────────────── 公开 API ──────────────────────────

    async def get_items(self, per_page: int = 100) -> list[dict]:
        """获取可交易物品列表。路径和字段名见 empire_config.py"""
        c = CFG["get_items"]
        return await self._request(
            c["method"], c["path"],
            params={**c.get("params", {}), "per_page": per_page},
        )

    async def get_socket_meta(self) -> dict:
        """获取 WS 认证凭证。路径见 empire_config.py"""
        c = CFG["get_socket_meta"]
        return await self._request(c["method"], c["path"])

    async def get_balance(self) -> dict:
        """获取用户余额（从 metadata/socket 接口取 balance 字段）。"""
        c = CFG["get_balance"]
        return await self._request(c["method"], c["path"])

    async def withdraw_item(self, item_id: str) -> dict:
        """购买/取回物品。路径和字段见 empire_config.py"""
        c = CFG["withdraw_item"]
        field = c["request_fields"]["item_id"]
        return await self._request(
            c["method"], c["path"], json_data={field: item_id},
        )

    async def place_auction_bid(self, auction_id: str, amount: float) -> dict:
        """拍卖出价。路径和字段见 empire_config.py"""
        c = CFG["place_auction_bid"]
        return await self._request(
            c["method"], c["path"],
            json_data={
                c["request_fields"]["auction_id"]: auction_id,
                c["request_fields"]["amount"]: amount,
            },
        )

    # ────────────────────────── 内部方法 ──────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
    ) -> Any:
        """统一请求方法：限流 → 请求 → 重试。"""
        client = await self._ensure_client()
        last_exc: Optional[Exception] = None

        for attempt in range(MAX_RETRIES + 1):
            await self.rate_limiter.acquire()

            try:
                response = await client.request(
                    method=method,
                    url=path,
                    params=params,
                    json=json_data,
                )

                if response.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                    logger.warning(
                        "%s %s → %d，第 %d 次重试，等待 %.0fs",
                        method, path, response.status_code, attempt + 1, wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                response.raise_for_status()

                # 尝试解析 JSON
                try:
                    return response.json()
                except Exception:
                    return response.text

            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                    logger.warning("请求超时 %s %s，重试 %d/3，等待 %.0fs", method, path, attempt + 1, wait)
                    await asyncio.sleep(wait)
                else:
                    raise EmpireHTTPError(
                        f"Empire API 请求超时: {method} {path}", status_code=408
                    ) from exc

            except httpx.HTTPStatusError as exc:
                raise EmpireHTTPError(
                    f"Empire API 错误 ({exc.response.status_code}): {method} {path}",
                    status_code=exc.response.status_code,
                ) from exc

            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                    logger.warning("请求失败 %s %s: %s，重试 %d/3", method, path, exc, attempt + 1)
                    await asyncio.sleep(wait)
                else:
                    raise EmpireHTTPError(f"Empire API 请求失败: {method} {path}") from exc

        # 理论上不会走到这里
        raise last_exc  # type: ignore[misc]
