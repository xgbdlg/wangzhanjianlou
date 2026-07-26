# services/multi_account.py
# 多账号并发管理器：为每个账号独立启动引擎和连接

import asyncio
import logging
from typing import Any, Optional

from sqlalchemy import select

from database import AsyncSessionLocal
from models import Account, StrategyConfig
from security import SecureStorage
from services.empire_http import EmpireHTTPClient
from services.market_engine import MarketSnipeEngine
from services.price_fetcher import CS2PriceFetcher

logger = logging.getLogger("multi_account")

# 每个账号的引擎集合
AccountEngines = dict[str, Any]


class MultiAccountManager:
    """多账号并发管理器。

    为每个账号独立创建 HTTP 客户端和监控引擎，
    使用 asyncio.gather() 并发启动/停止。

    使用方式:
        manager = MultiAccountManager(storage, price_fetcher)
        await manager.start_all()             # 启动所有账号
        await manager.start_account("主号")    # 单独启动某个
        await manager.stop_all()
    """

    def __init__(self, storage: SecureStorage, price_fetcher: CS2PriceFetcher):
        self.storage = storage
        self.price_fetcher = price_fetcher
        self.engines: dict[str, AccountEngines] = {}  # {account_name: {http, market, auction}}
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def active_accounts(self) -> list[str]:
        return list(self.engines.keys())

    # ────────────────────────── 全部启动/停止 ──────────────────────────

    async def start_all(self) -> list[str]:
        """遍历所有已注册账号，并发启动独立的监控引擎。

        Returns:
            成功启动的账号名列表
        """
        self._running = True
        account_data_list = self.storage.list_accounts()

        if not account_data_list:
            logger.warning("无可用账号，跳过启动")
            return []

        # 并发启动所有账号
        results = await asyncio.gather(
            *[self._safe_start_account(acc["name"]) for acc in account_data_list],
            return_exceptions=True,
        )

        started: list[str] = []
        for acc, result in zip(account_data_list, results):
            if isinstance(result, Exception):
                logger.error("启动账号 [%s] 失败: %s", acc["name"], result)
            else:
                started.append(acc["name"])

        logger.info("多账号启动完成: %d/%d 成功", len(started), len(account_data_list))
        return started

    async def stop_all(self) -> None:
        """并发停止所有账号的引擎和连接。"""
        self._running = False
        names = list(self.engines.keys())

        async def _stop_one(name: str):
            try:
                await self.stop_account(name)
            except Exception as exc:
                logger.warning("停止账号 [%s] 异常: %s", name, exc)

        await asyncio.gather(*[_stop_one(n) for n in names])
        self.engines.clear()
        logger.info("所有账号已停止")

    # ────────────────────────── 单账号启动/停止 ──────────────────────────

    async def start_account(self, name: str) -> bool:
        """为单个账号启动引擎。"""
        if not self._running:
            self._running = True
        return await self._safe_start_account(name)

    async def stop_account(self, name: str) -> None:
        """停止单个账号的引擎。"""
        engines = self.engines.pop(name, None)
        if engines is None:
            return

        # 停止引擎
        for key in ("market_engine", "auction_engine"):
            engine = engines.get(key)
            if engine:
                try:
                    if hasattr(engine, "stop_monitoring"):
                        await engine.stop_monitoring()
                    elif hasattr(engine, "stop"):
                        await engine.stop()
                except Exception as exc:
                    logger.warning("停止 [%s] %s 异常: %s", name, key, exc)

        # 关闭 HTTP 连接
        http_client = engines.get("http")
        if http_client:
            try:
                await http_client.close()
            except Exception:
                pass

        logger.info("账号 [%s] 已停止", name)

    # ────────────────────────── 内部 ──────────────────────────

    async def _safe_start_account(self, name: str) -> bool:
        """安全启动单个账号（失败不抛异常）。"""
        # 如果已在运行，先停
        if name in self.engines:
            await self.stop_account(name)

        account_data = self.storage.get_account(name)
        if not account_data:
            logger.warning("账号 [%s] 不在安全存储中", name)
            return False

        api_key = account_data["api_key"]
        empire_rate = account_data["empire_rate"]

        # 创建 HTTP 客户端
        http_client = EmpireHTTPClient(api_key=api_key, timeout=10.0)

        # 查询策略
        strategy = await self._load_strategy(name)

        # 创建市场引擎
        market_engine = MarketSnipeEngine(
            http_client=http_client,
            price_fetcher=self.price_fetcher,
            session_factory=AsyncSessionLocal,
            account_name=name,
            empire_rate=empire_rate,
        )
        if strategy:
            await market_engine.reload_strategy()

        await market_engine.start_monitoring()

        # 创建拍卖引擎（不自动连接 WS，等需要时再绑定）
        from services.auction_engine import AuctionSnipeEngine
        auction_engine = AuctionSnipeEngine(
            http_client=http_client,
            price_fetcher=self.price_fetcher,
            session_factory=AsyncSessionLocal,
            account_name=name,
            empire_rate=empire_rate,
        )
        if strategy:
            await auction_engine.reload_strategy()

        self.engines[name] = {
            "http": http_client,
            "market_engine": market_engine,
            "auction_engine": auction_engine,
        }

        logger.info("账号 [%s] 启动完成: rate=%.3f, market=%s",
                     name, empire_rate, market_engine.is_running)
        return True

    async def _load_strategy(self, account_name: str) -> Optional[StrategyConfig]:
        """加载账号专属策略。"""
        try:
            async with AsyncSessionLocal() as session:
                # 先查账号 ID
                acct = await session.execute(
                    select(Account.id).where(Account.name == account_name)
                )
                account_id = acct.scalars().first()

                strategy = None
                if account_id is not None:
                    result = await session.execute(
                        select(StrategyConfig).where(
                            StrategyConfig.account_id == account_id,
                            StrategyConfig.is_global == False,
                        )
                    )
                    strategy = result.scalars().first()

                if strategy is None:
                    result = await session.execute(
                        select(StrategyConfig).where(StrategyConfig.is_global == True)
                    )
                    strategy = result.scalars().first()

                return strategy
        except Exception as exc:
            logger.warning("加载策略 [%s] 失败: %s", account_name, exc)
            return None

    def get_engine(self, account_name: str, engine_type: str) -> Any:
        """获取指定账号的指定引擎。"""
        engines = self.engines.get(account_name, {})
        return engines.get(engine_type)
