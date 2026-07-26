# services/auction_engine.py
# 拍卖捡漏引擎 —— 实时监听 Empire WebSocket 拍卖事件，
# 计算折扣 → 自动出价 → 止损退出 → 记录历史。
#
# ⚠️ 注意：Empire WebSocket 事件名称基于推测，实际字段需抓包确认。

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio.session import async_sessionmaker

from models import AuctionDeal, BidRecord
from services.auction_state import (
    AUCTION_DURATION,
    AuctionItem,
    AuctionStatus,
    make_auction_item,
)
from services.name_normalizer import (
    check_wear_filter,
    match_blacklist,
    match_whitelist,
    normalize_name,
)

logger = logging.getLogger("auction_engine")

# 出价加价比例（每次在当前价基础上加 1%）
BID_INCREMENT_RATIO = 1.01


class AuctionSnipeEngine:
    """拍卖捡漏引擎 —— 实时竞价 + 止损管理。

    工作流程:
      1. 注册为 WS 消息回调，接收拍卖事件
      2. auction_started → 查 Buff 价 → 过滤 → 判定是否参与
      3. auction_bid    → 更新当前价 → 止损检查 → 自动加价
      4. auction_won     → 记录中标
      5. auction_expired → 清理过期

    使用方式:
        engine = AuctionSnipeEngine(
            http_client=empire_http,
            price_fetcher=cs2_fetcher,
            session_factory=AsyncSessionLocal,
            account_name="主账号",
            empire_rate=0.65,
        )
        # 注册为 WS 回调
        ws_client.on_message = lambda e, d: engine.handle_event({"event": e, "data": d})
    """

    def __init__(
        self,
        http_client,               # EmpireHTTPClient
        price_fetcher,             # CS2PriceFetcher
        session_factory: async_sessionmaker,
        account_name: str,
        empire_rate: float,
    ):
        self.http_client = http_client
        self.price_fetcher = price_fetcher
        self.session_factory = session_factory
        self.account_name = account_name
        self.empire_rate = empire_rate

        # 策略参数（可通过 reload_strategy 刷新）
        self.buff_rate: float = 0.138
        self.min_deal_pct: float = 15.0
        self.max_loss_pct: float = -5.0
        self.auto_bid: bool = False
        self.max_bid_usd: float = 500.0
        self.min_item_price: float = 5.0
        self.max_item_price: float = 2000.0
        self.whitelist: str = "[]"
        self.blacklist: str = "[]"
        self.wear_filter: str = "[]"

        # 活跃拍卖: {auction_id: AuctionItem}
        self.active_auctions: dict[str, AuctionItem] = {}
        self._running = False
        self._notify_callback: Optional[Callable] = None

        # 流拍重挂监控: {normalized_name: {original_bid, buff_price, expired_at}}
        self._relist_watch: dict[str, dict] = {}
        self._relist_hits: int = 0

        # 统计
        self._total_detected: int = 0
        self._total_bids: int = 0
        self._total_won: int = 0
        self._total_aborted: int = 0

    # ────────────────────────── 公开方法 ──────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "active_count": len(self.active_auctions),
            "total_detected": self._total_detected,
            "total_bids": self._total_bids,
            "total_won": self._total_won,
            "total_aborted": self._total_aborted,
        }

    def set_notify_callback(self, callback: Callable) -> None:
        """设置前端通知回调（用于 WebSocket 推送）。"""
        self._notify_callback = callback

    async def start(self) -> None:
        """启动引擎（开始接收事件）。"""
        self._running = True
        await self.reload_strategy()
        logger.info("拍卖引擎已启动: account=%s, auto_bid=%s", self.account_name, self.auto_bid)

    async def stop(self) -> None:
        """停止引擎。"""
        self._running = False
        # 将所有活跃拍卖标记为过期
        for auction in self.active_auctions.values():
            if auction.status not in (AuctionStatus.WON, AuctionStatus.ABORTED, AuctionStatus.EXPIRED):
                auction.transition_to(AuctionStatus.EXPIRED, "引擎停止")
                await self._update_db(auction)
        logger.info("拍卖引擎已停止: 活跃=%d, 出价=%d, 中标=%d",
                     len(self.active_auctions), self._total_bids, self._total_won)

    async def reload_strategy(self) -> None:
        """从数据库刷新策略配置。"""
        try:
            from models import Account, StrategyConfig

            async with self.session_factory() as session:
                result = await session.execute(
                    select(Account.id).where(Account.name == self.account_name)
                )
                account_id = result.scalars().first()

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

                if strategy:
                    self.buff_rate = strategy.buff_rate
                    self.min_deal_pct = strategy.min_deal_pct
                    self.max_loss_pct = strategy.max_loss_pct
                    self.auto_bid = strategy.auto_bid
                    self.max_bid_usd = strategy.max_bid_usd
                    self.min_item_price = strategy.min_item_price
                    self.max_item_price = strategy.max_item_price
                    self.whitelist = strategy.whitelist
                    self.blacklist = strategy.blacklist
                    self.wear_filter = strategy.wear_filter
                    logger.debug("拍卖策略已刷新: auto_bid=%s, min_deal=%.1f%%, max_loss=%.1f%%",
                                 self.auto_bid, self.min_deal_pct, self.max_loss_pct)
        except Exception as exc:
            logger.warning("加载拍卖策略失败: %s", exc)

    # ────────────────────────── 核心：事件分发 ──────────────────────────

    async def handle_event(self, event: dict) -> None:
        """WS 事件入口：根据 event["event"] 分发到对应处理方法。

        由 empire_ws.py 的消息回调调用。

        Args:
            event: {"event": "auction_started", "data": {...}}
        """
        if not self._running:
            return

        event_type = event.get("event", "")
        data = event.get("data", {})

        try:
            if event_type == "auction_started":
                await self._on_auction_started(data)
            elif event_type == "auction_bid":
                await self._on_auction_bid(data)
            elif event_type == "auction_won":
                await self._on_auction_won(data)
            elif event_type == "auction_expired":
                await self._on_auction_expired(data)
        except Exception as exc:
            logger.error("处理拍卖事件 [%s] 异常: %s", event_type, exc, exc_info=True)

    # ────────────────────────── 事件：拍卖开始 ──────────────────────────

    async def _on_auction_started(self, data: dict) -> None:
        """处理 auction_started 事件。

        ⚠️ 推测字段: id, market_hash_name, wear, base_price, starting_bid
        """
        # ⚠️ 推测字段名，需根据抓包调整
        auction_id = str(data.get("id") or data.get("auction_id", ""))
        if not auction_id:
            return

        raw_name = str(data.get("market_hash_name", ""))
        wear = str(data.get("wear", ""))
        base_price = float(data.get("base_price", 0))
        starting_bid = float(data.get("starting_bid", 0))

        if not raw_name or base_price <= 0:
            return

        standard_name = normalize_name(raw_name)

        # ── 过滤检查 ──
        empire_price_usd = base_price * self.empire_rate

        if empire_price_usd < self.min_item_price or empire_price_usd > self.max_item_price:
            return
        if not match_whitelist(standard_name, self.whitelist):
            return
        if match_blacklist(standard_name, self.blacklist):
            return
        if not check_wear_filter(wear, self.wear_filter):
            return

        # ── 查询 Buff 价格 ──
        try:
            price_data = await self.price_fetcher.get_price(standard_name, source="buff")
            buff_ask = price_data.get("ask", 0)
        except Exception as exc:
            logger.warning("拍卖查价失败 [%s]: %s", auction_id, exc)
            return

        if buff_ask <= 0:
            logger.debug("无 Buff 价格，跳过拍卖 [%s] %s", auction_id, standard_name)
            return

        buff_price_usd = buff_ask * self.buff_rate

        # ── 计算初始折扣 ──
        item = make_auction_item(
            auction_id=auction_id,
            market_hash_name=standard_name,
            wear=wear,
            base_price=base_price,
            starting_bid=starting_bid,
            buff_price_usd=buff_price_usd,
            account_name=self.account_name,
        )

        initial_discount = item.calculate_discount(self.empire_rate)
        item.max_discount_pct = initial_discount

        # ── 状态判定 ──
        if initial_discount >= self.min_deal_pct:
            # 折扣足够 → 进入竞价模式
            item.transition_to(AuctionStatus.BIDDING, f"初始折扣 {initial_discount:.1f}% ≥ {self.min_deal_pct}%")
            self._total_detected += 1

            # 如果开启自动出价，立即出第一手
            if self.auto_bid:
                await self._do_bid(item, is_first_bid=True)

        elif initial_discount >= self.max_loss_pct:
            # 折扣不够但未跌破止损 → 观望
            item.transition_to(AuctionStatus.WAITING, f"初始折扣 {initial_discount:.1f}% 在 [{self.max_loss_pct}%, {self.min_deal_pct}%)")
            self._total_detected += 1

        else:
            # 折扣跌破止损线 → 忽略
            logger.debug("拍卖折扣过低 [%s] %s: %.1f%% < %.1f%%",
                         auction_id, standard_name, initial_discount, self.max_loss_pct)
            return

        # ── 流拍重挂检查 ──
        normalized = standard_name.lower()
        if normalized in self._relist_watch:
            prev = self._relist_watch[normalized]
            prev_bid = prev["original_bid"]
            if starting_bid < prev_bid:
                self._relist_hits += 1
                logger.info("🔁 流拍重挂: %s | 原价 %.1f → 新价 %.1f (-%.1f%%)",
                            standard_name, prev_bid, starting_bid,
                            (prev_bid - starting_bid) / max(prev_bid, 1) * 100)
                await self._notify(item, f"🔁 流拍重挂! 降价 {prev_bid - starting_bid:.0f} 币")

        # ── 加入活跃列表 + 写入 DB + 启动过期定时器 ──
        self.active_auctions[auction_id] = item
        await self._save_auction_deal(item)
        asyncio.create_task(self._expire_timer(auction_id))
        await self._notify(item, f"拍卖开始: 折扣 {initial_discount:.1f}%")

    # ────────────────────────── 事件：有人出价 ──────────────────────────

    async def _on_auction_bid(self, data: dict) -> None:
        """处理 auction_bid 事件。

        逻辑:
          1. 更新 current_bid → 重新计算折扣
          2. 止损检查（最高优先级）
          3. 如果折扣恢复，重新竞价
          4. 如果 auto_bid 开启，自动加价

        ⚠️ 推测字段: auction_id, new_bid, bidder_name
        """
        auction_id = str(data.get("auction_id") or data.get("id", ""))
        if not auction_id or auction_id not in self.active_auctions:
            return

        item = self.active_auctions[auction_id]
        if item.status in (AuctionStatus.WON, AuctionStatus.ABORTED, AuctionStatus.EXPIRED):
            return

        # 更新当前价
        new_bid = float(data.get("new_bid") or data.get("amount", 0))
        if new_bid <= item.current_bid:
            return  # 出价没有涨，忽略

        item.current_bid = new_bid
        discount = item.calculate_discount(self.empire_rate)
        if discount > item.max_discount_pct:
            item.max_discount_pct = discount

        # ═══════════════════════════════════════════════
        # 止损检查（最高优先级）
        # ═══════════════════════════════════════════════

        # 止损线跌破 → 放弃竞拍
        if discount < self.max_loss_pct:
            item.transition_to(AuctionStatus.ABORTED, f"折扣 {discount:.1f}% < 止损线 {self.max_loss_pct}%")
            self._total_aborted += 1
            await self._update_db(item)
            await self._notify(item, f"🛑 止损退出: 折扣 {discount:.1f}% 跌破止损线 {self.max_loss_pct}%")
            return

        # 折扣下降但未跌破止损 → 进入观望
        if discount < self.min_deal_pct and item.status == AuctionStatus.BIDDING:
            item.transition_to(AuctionStatus.WAITING, f"折扣降至 {discount:.1f}%，停止出价观望")
            await self._notify(item, f"⏸️ 观望: 折扣降至 {discount:.1f}%")

        # 折扣恢复 → 重新出价
        if discount >= self.min_deal_pct and item.status == AuctionStatus.WAITING:
            item.transition_to(AuctionStatus.BIDDING, f"折扣恢复至 {discount:.1f}%")
            if self.auto_bid:
                await self._do_bid(item)
            else:
                await self._notify(item, f"▶️ 折扣恢复: {discount:.1f}%，可继续竞价")

        # 已在竞价中 + 自动出价 → 加价
        if discount >= self.min_deal_pct and item.status == AuctionStatus.BIDDING and self.auto_bid:
            await self._do_bid(item)

    # ────────────────────────── 事件：竞拍成功 ──────────────────────────

    async def _on_auction_won(self, data: dict) -> None:
        """处理 auction_won 事件。

        ⚠️ 推测字段: auction_id, winner_name, final_bid
        """
        auction_id = str(data.get("auction_id") or data.get("id", ""))
        if not auction_id or auction_id not in self.active_auctions:
            return

        item = self.active_auctions[auction_id]
        final_bid = float(data.get("final_bid", item.current_bid))

        # ⚠️ 推测：判断是否自己中标（需根据实际 API 调整）
        winner = str(data.get("winner_name", ""))
        is_me = winner == self.account_name or item.bid_count > 0  # 简化判断

        if is_me:
            item.current_bid = final_bid
            item.transition_to(AuctionStatus.WON, f"竞拍成功! 最终价: {final_bid}")
            self._total_won += 1
            await self._notify(item, f"🎉 竞拍成功! 最终价: {final_bid}")
        else:
            item.transition_to(AuctionStatus.EXPIRED, "被他人拍得")
            await self._notify(item, "拍卖结束: 未中标")

        await self._update_db(item, final_bid=final_bid, won_by_me=is_me)

    # ────────────────────────── 事件：拍卖过期 ──────────────────────────

    async def _on_auction_expired(self, data: dict) -> None:
        """处理 auction_expired 事件 + 标记流拍重挂监控。"""
        auction_id = str(data.get("auction_id") or data.get("id", ""))
        if not auction_id or auction_id not in self.active_auctions:
            return

        item = self.active_auctions[auction_id]
        if item.status not in (AuctionStatus.WON, AuctionStatus.ABORTED, AuctionStatus.EXPIRED):
            item.transition_to(AuctionStatus.EXPIRED, "拍卖过期")

        # ── 流拍重挂监控 ──
        # 记录该物品信息，1 小时内同名物品重新上架时触发重新评估
        normalized = item.market_hash_name.lower()
        if normalized not in self._relist_watch:
            self._relist_watch[normalized] = {
                "original_bid": item.current_bid,
                "buff_price": item.buff_price_usd,
                "expired_at": datetime.now(timezone.utc).isoformat(),
            }
            logger.info("流拍重挂监控已注册: %s (1h)", item.market_hash_name)
            # 1 小时后自动清除
            import asyncio as _asyncio
            _asyncio.create_task(self._clear_relist_watch(normalized, delay=3600))

        await self._update_db(item)

    # ────────────────────────── 出价逻辑 ──────────────────────────

    async def _do_bid(self, item: AuctionItem, is_first_bid: bool = False) -> bool:
        """执行出价操作（自动/手动）。

        出价流程:
          1. 计算 next_bid = current_bid × 1.01（加价 1%）
          2. 预判: next_discount = (buff - next_bid × rate) / buff × 100
          3. 如果 next_discount ≥ max_loss_pct → 安全，执行出价
          4. 如果 next_discount < max_loss_pct → 放弃，改为 WAITING

        ⚠️ 推测接口：place_auction_bid 使用 POST /api/v2/trading/auction/bid
        实际出价 API 需抓包确认。

        Returns:
            bool: 是否成功出价
        """
        if is_first_bid:
            next_bid = item.current_bid  # 第一手不出价，只记录
        else:
            next_bid = item.current_bid * BID_INCREMENT_RATIO

        # ── 预判出价后的折扣 ──
        next_price_usd = next_bid * self.empire_rate
        next_discount = self._safe_discount(item.buff_price_usd, next_price_usd)

        # ── 止损预判：出价后是否会亏损 ──
        if next_discount < self.max_loss_pct:
            item.transition_to(AuctionStatus.WAITING, f"预判出价后折扣 {next_discount:.1f}% < 止损线 {self.max_loss_pct}%")
            await self._notify(item, f"⚠️ 预判出价后折扣 {next_discount:.1f}%，放弃出价，观望中")
            return False

        # ── 检查出价上限 ──
        if next_price_usd > self.max_bid_usd:
            item.transition_to(AuctionStatus.WAITING, f"出价 ${next_price_usd:.2f} > 上限 ${self.max_bid_usd}")
            await self._notify(item, f"⚠️ 出价 ${next_price_usd:.2f} 超上限，放弃出价")
            return False

        # ── 执行出价 ──
        try:
            result = await asyncio.wait_for(
                self.http_client.place_auction_bid(item.id, next_bid),
                timeout=10.0,
            )
            logger.info("出价 [%s]: bid=%.1f, price_usd=%.2f, discount=%.1f%%",
                        item.id, next_bid, next_price_usd, next_discount)

            item.current_bid = next_bid
            item.bid_count += 1
            self._total_bids += 1

            # 记录出价历史
            await self._save_bid_record(item, next_bid, next_price_usd, next_discount)
            await self._notify(item, f"💰 已出价: {next_bid:.1f} (${next_price_usd:.2f}) | 折扣 {next_discount:.1f}%")

            return True

        except asyncio.TimeoutError:
            logger.warning("出价超时 [%s]", item.id)
            return False
        except Exception as exc:
            logger.error("出价失败 [%s]: %s", item.id, exc)
            return False

    # ────────────────────────── 手动操作 ──────────────────────────

    async def manual_bid(self, auction_id: str, amount: Optional[float] = None) -> bool:
        """手动出价。

        Args:
            auction_id: 拍卖 ID
            amount: 出价金额。不传 = 自动计算 current_bid × 1.01
        """
        if auction_id not in self.active_auctions:
            return False

        item = self.active_auctions[auction_id]
        if amount:
            item.current_bid = amount / self.empire_rate  # 反推平台币价格
        else:
            amount = item.current_bid * BID_INCREMENT_RATIO * self.empire_rate

        return await self._do_bid(item)

    async def manual_abort(self, auction_id: str) -> bool:
        """手动放弃竞拍。"""
        if auction_id not in self.active_auctions:
            return False

        item = self.active_auctions[auction_id]
        if item.status in (AuctionStatus.WON, AuctionStatus.ABORTED, AuctionStatus.EXPIRED):
            return False

        item.transition_to(AuctionStatus.ABORTED, "手动放弃")
        self._total_aborted += 1
        await self._update_db(item)
        await self._notify(item, "手动放弃竞拍")
        return True

    # ────────────────────────── 过期定时器 ──────────────────────────

    async def _expire_timer(self, auction_id: str) -> None:
        """3 分钟过期定时器。

        到时间后，如果拍卖仍在活跃列表中且未结束，自动标记为过期。
        """
        await asyncio.sleep(AUCTION_DURATION)

        if auction_id not in self.active_auctions:
            return

        item = self.active_auctions[auction_id]
        if item.status in (AuctionStatus.WON, AuctionStatus.ABORTED, AuctionStatus.EXPIRED):
            return

        item.transition_to(AuctionStatus.EXPIRED, "3 分钟定时器到期")
        await self._update_db(item)
        await self._notify(item, "⏰ 拍卖已过期")
        logger.info("拍卖过期 [%s] %s", auction_id, item.market_hash_name)

    # ────────────────────────── 数据库操作 ──────────────────────────

    async def _save_auction_deal(self, item: AuctionItem) -> None:
        """写入/更新 AuctionDeal 表。"""
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(AuctionDeal).where(AuctionDeal.id == item.id)
                )
                deal = result.scalars().first()

                if deal is None:
                    deal = AuctionDeal(
                        id=item.id,
                        market_hash_name=item.market_hash_name,
                        wear=item.wear or None,
                        base_price=item.base_price,
                        starting_bid=item.starting_bid,
                        buff_price_usd=item.buff_price_usd,
                        max_discount_pct=item.max_discount_pct,
                        status=item.status.value,
                        account_name=item.account_name,
                    )
                else:
                    deal.current_bid = item.current_bid  # 类型: ignore
                    deal.max_discount_pct = max(deal.max_discount_pct, item.max_discount_pct)
                    deal.status = item.status.value

                session.add(deal)
                await session.commit()
        except Exception as exc:
            logger.error("写入 AuctionDeal [%s] 失败: %s", item.id, exc)

    async def _update_db(
        self, item: AuctionItem, final_bid: float = 0, won_by_me: bool = False
    ) -> None:
        """更新拍卖数据库记录（结束状态）。"""
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(AuctionDeal).where(AuctionDeal.id == item.id)
                )
                deal = result.scalars().first()
                if deal:
                    deal.status = item.status.value
                    deal.max_discount_pct = max(deal.max_discount_pct, item.max_discount_pct)
                    if final_bid:
                        deal.final_bid = final_bid
                    if won_by_me:
                        deal.won_by_me = True
                    if item.status in (AuctionStatus.WON, AuctionStatus.ABORTED, AuctionStatus.EXPIRED):
                        deal.ended_at = datetime.now(timezone.utc)
                    session.add(deal)
                    await session.commit()
        except Exception as exc:
            logger.error("更新 AuctionDeal [%s] 失败: %s", item.id, exc)

    async def _save_bid_record(
        self, item: AuctionItem, bid_amount: float, bid_usd: float, discount: float
    ) -> None:
        """记录出价历史到 BidRecord 表。"""
        try:
            async with self.session_factory() as session:
                record = BidRecord(
                    auction_id=item.id,
                    account_name=item.account_name,
                    bid_amount=bid_amount,
                    bid_amount_usd=round(bid_usd, 4),
                    discount_at_bid=round(discount, 2),
                    is_auto=self.auto_bid,
                )
                session.add(record)
                await session.commit()
        except Exception as exc:
            logger.error("写入 BidRecord [%s] 失败: %s", item.id, exc)

    # ────────────────────────── 通知 ──────────────────────────

    async def _notify(self, item: AuctionItem, message: str) -> None:
        """发送通知（日志 + 可选前端推送）。"""
        logger.info("[%s] %s: %s", item.id, item.market_hash_name, message)

        if self._notify_callback:
            try:
                await self._notify_callback({
                    "auction_id": item.id,
                    "name": item.market_hash_name,
                    "message": message,
                    "discount_pct": round(item.calculate_discount(self.empire_rate), 2),
                    "status": item.status.value,
                    "bid_count": item.bid_count,
                })
            except Exception as exc:
                logger.error("通知回调异常: %s", exc)

    # ────────────────────────── 辅助 ──────────────────────────

    async def _clear_relist_watch(self, normalized_name: str, delay: int = 3600) -> None:
        """延迟清除流拍重挂监控记录。"""
        await asyncio.sleep(delay)
        self._relist_watch.pop(normalized_name, None)
        logger.debug("流拍重挂监控已清除: %s", normalized_name)

    @staticmethod
    def _safe_discount(buff_price: float, empire_price: float) -> float:
        """安全计算折扣，防止除零。"""
        if buff_price <= 0:
            return -999.0
        return (buff_price - empire_price) / buff_price * 100.0
