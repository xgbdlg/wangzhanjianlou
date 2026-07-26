# services/market_engine.py
# CSGOEmpire P2P 市场捡漏引擎 —— 核心模块
#
# 轮询 Empire 市场物品 → 对比 Buff 价格 → 计算折扣 → 过滤 → 自动购买
#
# ⚠️ 注意：Empire API 接口（get_items / withdraw_item）基于推测，
# 实际响应格式和参数需根据抓包确认后调整。

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio.session import async_sessionmaker

from models import Account, MarketDeal, PurchaseRecord, StrategyConfig
from services.name_normalizer import (
    check_wear_filter,
    match_blacklist,
    match_whitelist,
    normalize_name,
)

logger = logging.getLogger("market_engine")

# 默认轮询间隔（秒）
DEFAULT_POLL_INTERVAL = 7.0

# ⚠️ 推测：Empire API 返回的物品状态值
ACTIVE_STATUSES = {"active", "available", "listed"}


class MarketSnipeEngine:
    """P2P 市场捡漏引擎。

    工作流程：
      1. 每 N 秒轮询 Empire 市场物品列表
      2. 批量查询 Buff 价格
      3. 对每个物品计算折扣率
      4. 根据策略过滤（价格区间、白/黑名单、磨损）
      5. 检测到的漏单写入 MarketDeal 表
      6. 可选自动购买

    使用方式:
        engine = MarketSnipeEngine(
            http_client=empire_http,
            price_fetcher=cs2_fetcher,
            session_factory=AsyncSessionLocal,
            account_name="主账号",
            empire_rate=0.65,
        )
        await engine.start_monitoring()
    """

    def __init__(
        self,
        http_client,               # EmpireHTTPClient
        price_fetcher,             # CS2PriceFetcher
        session_factory: async_sessionmaker,
        account_name: str,
        empire_rate: float,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ):
        self.http_client = http_client
        self.price_fetcher = price_fetcher
        self.session_factory = session_factory
        self.account_name = account_name
        self.empire_rate = empire_rate
        self.poll_interval = poll_interval

        # 策略配置（可通过 reload_strategy() 刷新）
        self.buff_rate: float = 0.138
        self.min_deal_pct: float = 15.0
        self.max_loss_pct: float = -5.0
        self.auto_buy: bool = False
        self.max_buy_usd: float = 500.0
        self.min_item_price: float = 5.0
        self.max_item_price: float = 2000.0
        self.whitelist: str = "[]"
        self.blacklist: str = "[]"
        self.wear_filter: str = "[]"

        # 去重：已见过的 item_id（防止重复通知/购买同一物品）
        self.seen_item_ids: set[str] = set()

        # 运行时状态
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._cycle_count: int = 0
        self._deals_found: int = 0
        self._items_bought: int = 0

    # ────────────────────────── 公开方法 ──────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "cycle_count": self._cycle_count,
            "deals_found": self._deals_found,
            "items_bought": self._items_bought,
            "seen_items": len(self.seen_item_ids),
            "poll_interval": self.poll_interval,
        }

    async def start_monitoring(self) -> None:
        """启动后台轮询任务。

        启动前自动从 DB 恢复已见过的物品 ID（防止重启后重复购买）。
        """
        if self._running:
            logger.warning("引擎已在运行中")
            return

        # 从数据库恢复已见过的物品 ID
        await self._load_seen_items()

        # 加载最新策略
        await self.reload_strategy()

        self._running = True
        self._task = asyncio.create_task(self._polling_loop())
        logger.info(
            "捡漏引擎已启动: account=%s, poll=%.1fs, min_deal=%.1f%%, auto_buy=%s",
            self.account_name, self.poll_interval, self.min_deal_pct, self.auto_buy,
        )

    async def stop_monitoring(self) -> None:
        """停止轮询任务并清理。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("捡漏引擎已停止: account=%s, 共检测 %d 个漏单, 购买 %d 个",
                     self.account_name, self._deals_found, self._items_bought)

    async def reload_strategy(self) -> None:
        """从数据库重新加载当前账号的策略配置。"""
        try:
            async with self.session_factory() as session:
                # 先查账号 ID
                acct_result = await session.execute(
                    select(Account.id).where(Account.name == self.account_name)
                )
                account_id = acct_result.scalars().first()

                strategy = None
                # 查询账号专属策略
                if account_id is not None:
                    result = await session.execute(
                        select(StrategyConfig).where(
                            StrategyConfig.account_id == account_id,
                            StrategyConfig.is_global == False,
                        )
                    )
                    strategy = result.scalars().first()

                # 如果没有账号专属策略，用全局策略
                if strategy is None:
                    result = await session.execute(
                        select(StrategyConfig).where(StrategyConfig.is_global == True)
                    )
                    strategy = result.scalars().first()

                if strategy:
                    self.buff_rate = strategy.buff_rate
                    self.min_deal_pct = strategy.min_deal_pct
                    self.max_loss_pct = strategy.max_loss_pct
                    self.auto_buy = strategy.auto_buy
                    self.max_buy_usd = strategy.max_buy_usd
                    self.min_item_price = strategy.min_item_price
                    self.max_item_price = strategy.max_item_price
                    self.whitelist = strategy.whitelist
                    self.blacklist = strategy.blacklist
                    self.wear_filter = strategy.wear_filter
                    logger.debug("策略已刷新: min_deal=%.1f%%, auto_buy=%s", self.min_deal_pct, self.auto_buy)
        except Exception as exc:
            logger.warning("加载策略失败，使用内存中的旧策略: %s", exc)

    # ────────────────────────── 核心轮询循环 ──────────────────────────

    async def _polling_loop(self) -> None:
        """主轮询循环：获取市场物品 → 查价 → 计算折扣 → 过滤 → 购买。"""
        await asyncio.sleep(2)  # 启动后稍等

        while self._running:
            self._cycle_count += 1
            cycle_start = datetime.now(timezone.utc)

            try:
                # ── 步骤 1: 获取市场物品列表 ──
                items = await self._fetch_market_items()
                if not items:
                    logger.debug("[周期 %d] 市场无物品或获取失败", self._cycle_count)
                    await self._sleep()
                    continue

                # ── 步骤 2: 过滤状态 + 去重 + 提取为查价列表 ──
                fresh_items = self._filter_and_dedup(items)
                if not fresh_items:
                    logger.debug("[周期 %d] 获取 %d 个物品，0 个新物品",
                                 self._cycle_count, len(items))
                    await self._sleep()
                    continue

                logger.info("[周期 %d] 获取 %d 个物品，%d 个新物品待查价",
                            self._cycle_count, len(items), len(fresh_items))

                # ── 步骤 3: 批量查询 Buff 价格 ──
                item_names = [item["market_hash_name"] for item in fresh_items]
                prices = await self._batch_fetch_prices(item_names)

                # ── 步骤 4+5+6: 逐物品评估 → 过滤 → 捡漏判定 → 购买 ──
                for item in fresh_items:
                    await self._evaluate_item(item, prices)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[周期 %d] 异常: %s", self._cycle_count, exc, exc_info=True)

            await self._sleep()

    # ────────────────────────── 步骤 1: 获取市场物品 ──────────────────────────

    async def _fetch_market_items(self) -> list[dict]:
        """调用 Empire HTTP API 获取市场物品列表。

        ⚠️ 推测接口：GET /api/v2/trading/items 返回格式需抓包确认。
        预期每个物品包含: id, market_hash_name, wear, price(coins), status
        """
        try:
            response = await asyncio.wait_for(
                self.http_client.get_items(per_page=100),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            logger.warning("获取市场物品超时")
            return []
        except Exception as exc:
            logger.warning("获取市场物品失败: %s", exc)
            return []

        # ⚠️ 推测：响应格式可能是 {"data": [...]} 或直接是列表
        if isinstance(response, dict):
            items = response.get("data", response.get("items", []))
        elif isinstance(response, list):
            items = response
        else:
            logger.warning("未知的市场物品响应格式: %s", type(response))
            return []

        if not isinstance(items, list):
            return []

        return items

    # ────────────────────────── 步骤 2: 过滤 + 去重 ──────────────────────────

    def _filter_and_dedup(self, items: list[dict]) -> list[dict]:
        """过滤掉不活跃/已见过的物品，返回新物品列表。

        过滤规则：
        1. status 必须在 ACTIVE_STATUSES 中
        2. item_id 不在 seen_item_ids 中（去重）
        """
        fresh: list[dict] = []

        for item in items:
            # ⚠️ 推测：字段名可能为 id / item_id / _id
            item_id = str(item.get("id") or item.get("item_id") or item.get("_id", ""))
            if not item_id:
                continue

            status = str(item.get("status", "")).lower()
            if status not in ACTIVE_STATUSES:
                continue

            if item_id in self.seen_item_ids:
                continue

            # 统一字段名方便后续使用
            item["_item_id"] = item_id
            item["_market_hash_name"] = str(item.get("market_hash_name", ""))
            item["_wear"] = item.get("wear") or ""
            item["_price"] = float(item.get("price", 0))

            fresh.append(item)

        return fresh

    # ────────────────────────── 步骤 3: 批量查价 ──────────────────────────

    async def _batch_fetch_prices(self, names: list[str]) -> dict:
        """批量查询 Buff 价格（最多 100 个）。"""
        try:
            return await self.price_fetcher.batch_get_prices(names, source="buff")
        except Exception as exc:
            logger.warning("批量查价失败: %s", exc)
            return {}

    # ────────────────────────── 步骤 4+5+6: 逐物品评估 ──────────────────────────

    async def _evaluate_item(self, item: dict, prices: dict) -> None:
        """评估单个物品：计算折扣 → 过滤 → 判定 → 购买。

        这是捡漏引擎的核心算法，每个步骤都有详细注释。
        """
        item_id = item["_item_id"]
        name = item["_market_hash_name"]
        wear = item["_wear"]
        price_coins = item["_price"]   # Empire 平台币价格

        # ── 4a: 价格换算 ──
        # Empire 价格: 平台币 × 汇率 → USD
        empire_price_usd = price_coins * self.empire_rate

        # Buff 价格: 从查价结果取 ask 价（最低卖价）
        buff_data = prices.get(name, {})
        buff_ask = buff_data.get("ask", 0.0)

        if buff_ask <= 0:
            # 无 Buff 价格则无法评估，跳过
            return

        # Buff 价格 × buff_rate → 换算后的 USD 参考价
        buff_price_usd = buff_ask * self.buff_rate

        # ── 4b: 折扣计算 ──
        # discount_pct = (Buff价 - Empire价) / Buff价 × 100
        # 正数 = 有折扣（Empire 比 Buff 便宜）
        # 负数 = 溢价（Empire 比 Buff 贵）
        if buff_price_usd <= 0:
            return

        discount_pct = (buff_price_usd - empire_price_usd) / buff_price_usd * 100.0

        # ── 5: 过滤检查 ──

        # 5a: 价格区间过滤
        if empire_price_usd < self.min_item_price or empire_price_usd > self.max_item_price:
            return

        # 5b: 白名单过滤（如果设置了白名单，名称必须匹配）
        if not match_whitelist(name, self.whitelist):
            return

        # 5c: 黑名单过滤（匹配黑名单则跳过）
        if match_blacklist(name, self.blacklist):
            return

        # 5d: 磨损过滤（如果设置了磨损过滤，必须在列表中）
        if not check_wear_filter(wear, self.wear_filter):
            return

        # ── 6: 捡漏判定 ──

        self.seen_item_ids.add(item_id)

        if discount_pct < self.min_deal_pct:
            return  # 折扣不够，忽略

        # ✅ 检测到漏单！
        self._deals_found += 1
        standard_name = normalize_name(name)

        deal_status = "detected"
        bought_at = None

        logger.info(
            "[捡漏] %s | 折扣 %.1f%% | Empire: $%.2f (%d coins) | Buff: $%.2f | 磨损: %s",
            standard_name, discount_pct, empire_price_usd, int(price_coins),
            buff_price_usd, wear or "未知",
        )

        # ── 自动购买 ──
        if self.auto_buy:
            if empire_price_usd <= self.max_buy_usd:
                # ⚠️ 推测接口：withdraw_item 实现购买
                buy_result = await self._execute_buy(item_id)

                if buy_result.get("success"):
                    deal_status = "bought"
                    bought_at = datetime.now(timezone.utc)
                    self._items_bought += 1
                    logger.info("[购买成功] %s | $%.2f", standard_name, empire_price_usd)
                else:
                    error = buy_result.get("error", "未知错误")
                    logger.warning("[购买失败] %s | %s", standard_name, error)
            else:
                logger.info(
                    "[跳过购买] %s | $%.2f > max_buy_usd $%.2f",
                    standard_name, empire_price_usd, self.max_buy_usd,
                )

        # ── 写入 MarketDeal 表 ──
        await self._save_deal(
            item_id=item_id,
            market_hash_name=standard_name,
            wear=wear,
            empire_price_usd=round(empire_price_usd, 4),
            buff_price_usd=round(buff_price_usd, 4),
            discount_pct=round(discount_pct, 2),
            status=deal_status,
            bought_at=bought_at,
        )

        # ── 通知 ──
        await self.notify_deal({
            "item_id": item_id,
            "name": standard_name,
            "wear": wear,
            "empire_price": round(empire_price_usd, 4),
            "buff_price": round(buff_price_usd, 4),
            "discount_pct": round(discount_pct, 2),
            "status": deal_status,
            "auto_buy": self.auto_buy,
        })

        # ── 如果购买成功，写 PurchaseRecord ──
        if deal_status == "bought" and bought_at:
            await self._save_purchase(
                item_id=item_id,
                market_hash_name=standard_name,
                purchase_price_usd=round(empire_price_usd, 4),
                buff_price_usd=round(buff_price_usd, 4),
                discount_pct=round(discount_pct, 2),
            )

    # ────────────────────────── 自动购买 ──────────────────────────

    async def _execute_buy(self, item_id: str) -> dict:
        """执行购买操作。

        ⚠️ 推测接口：POST /api/v2/trading/withdraw
        实际 API 路径和参数需根据抓包确认。
        """
        try:
            result = await asyncio.wait_for(
                self.http_client.withdraw_item(item_id),
                timeout=15.0,
            )
            # ⚠️ 推测：成功响应格式需确认
            if isinstance(result, dict):
                # 假设成功返回包含 status 或 success
                if result.get("status") == "ok" or result.get("success"):
                    return {"success": True, "data": result}
                return {"success": False, "error": result.get("message", "购买返回异常状态"), "data": result}
            return {"success": True, "data": result}
        except asyncio.TimeoutError:
            return {"success": False, "error": "购买请求超时"}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ────────────────────────── 数据库写入 ──────────────────────────

    async def _save_deal(
        self,
        item_id: str,
        market_hash_name: str,
        wear: str,
        empire_price_usd: float,
        buff_price_usd: float,
        discount_pct: float,
        status: str,
        bought_at: Optional[datetime] = None,
    ) -> None:
        """写入 MarketDeal 表。"""
        try:
            async with self.session_factory() as session:
                deal = MarketDeal(
                    item_id=item_id,
                    market_hash_name=market_hash_name,
                    wear=wear or None,
                    empire_price_usd=empire_price_usd,
                    buff_price_usd=buff_price_usd,
                    discount_pct=discount_pct,
                    status=status,
                    account_name=self.account_name,
                    bought_at=bought_at,
                )
                session.add(deal)
                await session.commit()
        except Exception as exc:
            logger.error("写入 MarketDeal 失败: %s", exc)

    async def _save_purchase(
        self,
        item_id: str,
        market_hash_name: str,
        purchase_price_usd: float,
        buff_price_usd: float,
        discount_pct: float,
    ) -> None:
        """写入 PurchaseRecord 表。"""
        try:
            async with self.session_factory() as session:
                record = PurchaseRecord(
                    item_id=item_id,
                    market_hash_name=market_hash_name,
                    purchase_price_usd=purchase_price_usd,
                    buff_price_usd=buff_price_usd,
                    discount_pct=discount_pct,
                    is_auto=True,
                    account_name=self.account_name,
                )
                session.add(record)
                await session.commit()
        except Exception as exc:
            logger.error("写入 PurchaseRecord 失败: %s", exc)

    # ────────────────────────── 去重恢复 ──────────────────────────

    async def _load_seen_items(self) -> None:
        """从数据库恢复今日已见过的物品 ID（防止重启后重复处理）。"""
        try:
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            async with self.session_factory() as session:
                result = await session.execute(
                    select(MarketDeal.item_id).where(
                        MarketDeal.created_at >= today_start,
                    )
                )
                ids = {row[0] for row in result.fetchall()}
                self.seen_item_ids.update(ids)
                logger.info("从数据库恢复 %d 个已见物品 ID（今日）", len(ids))
        except Exception as exc:
            logger.warning("恢复已见物品 ID 失败: %s", exc)

    # ────────────────────────── 通知 ──────────────────────────

    async def notify_deal(self, deal: dict) -> None:
        """发送漏单通知。

        当前实现：打印日志。后续通过 WebSocket 推送给前端插件。
        """
        # Placeholder: 后续通过 EmpireWebSocketClient 或独立的推送通道通知前端
        logger.info(
            "[通知] %s | 折扣 %.1f%% | $%.2f → $%.2f | %s",
            deal["name"], deal["discount_pct"],
            deal["empire_price"], deal["buff_price"],
            "已自动购买" if deal["status"] == "bought" else "待手动购买",
        )

    # ────────────────────────── 内部辅助 ──────────────────────────

    async def _sleep(self) -> None:
        """轮询间隔等待，支持随时取消。"""
        try:
            await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            raise
