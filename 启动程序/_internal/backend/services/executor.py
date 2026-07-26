# services/executor.py
# 交易执行层：购买 + 出价 + 余额检查 + 订单跟踪
#
# ⚠️ 注意：所有 Empire 交易接口均为推测，实际需根据抓包调整。

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio.session import async_sessionmaker

from models import BidRecord, PurchaseRecord
from services.empire_http import EmpireHTTPClient, EmpireHTTPError

logger = logging.getLogger("executor")

# 余额缓冲比例（留 5% 防止汇率波动或手续费）
BALANCE_BUFFER_RATIO = 1.05

# 默认余额监控间隔（秒）
BALANCE_CHECK_INTERVAL = 30.0

# 低余额警告阈值（USD）
LOW_BALANCE_THRESHOLD = 50.0


class TradeError(Exception):
    """交易执行异常。"""
    def __init__(self, message: str, reason: str = "", status_code: Optional[int] = None):
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


class TradeExecutor:
    """交易执行器：封装购买/出价的余额检查、执行、记录和错误处理。

    使用方式:
        executor = TradeExecutor(http_client=empire_http, session_factory=AsyncSessionLocal)
        result = await executor.buy_item("id_123", "AK-47 | Redline", 5.20, is_auto=False)
    """

    def __init__(
        self,
        http_client: EmpireHTTPClient,
        session_factory: async_sessionmaker,
    ):
        self.http_client = http_client
        self.session_factory = session_factory

        # 待确认订单队列（用于跟踪异步确认的交易）
        self.pending_orders: dict[str, dict] = {}

    # ────────────────────────── 购买 ──────────────────────────

    async def buy_item(
        self,
        item_id: str,
        item_name: str,
        price_usd: float,
        account_name: str,
        buff_price_usd: float = 0.0,
        discount_pct: float = 0.0,
        is_auto: bool = False,
    ) -> dict:
        """执行购买操作。

        流程:
          1. 检查余额 → 2. 调用购买 API → 3. 写入 PurchaseRecord

        Args:
            item_id: Empire 物品 ID
            item_name: 皮肤市场名
            price_usd: 预期支付价格（USD）
            account_name: 执行购买的账号名
            buff_price_usd: Buff 参考价（用于记录）
            discount_pct: 折扣率（用于记录）
            is_auto: 是否自动购买

        Returns:
            {"status": "success"|"failed", "purchase_id"?: int, "reason"?: str}

        ⚠️ 推测接口：withdraw_item 的实际路径和参数需抓包确认。
        """
        # ── 步骤 a: 余额检查 ──
        try:
            balance_data = await asyncio.wait_for(
                self.http_client.get_balance(),
                timeout=10.0,
            )
            balance_usd = float(balance_data.get("balance_usd", 0))
            required = price_usd * BALANCE_BUFFER_RATIO

            if balance_usd < required:
                return {
                    "status": "failed",
                    "reason": f"余额不足: 需要 ${required:.2f}（含 5% 缓冲），当前 ${balance_usd:.2f}",
                    "balance": balance_usd,
                    "required": round(required, 2),
                }
        except asyncio.TimeoutError:
            return {"status": "failed", "reason": "余额查询超时，购买取消"}
        except Exception as exc:
            logger.warning("余额查询失败: %s，继续尝试购买", exc)

        # ── 步骤 b: 执行购买 ──
        try:
            result = await asyncio.wait_for(
                self.http_client.withdraw_item(item_id),
                timeout=15.0,
            )
            logger.info("购买请求已发送: item_id=%s, name=%s, price=%.2f", item_id, item_name, price_usd)

        except asyncio.TimeoutError:
            return {
                "status": "failed",
                "reason": "购买请求超时，物品可能已被抢走",
                "item_id": item_id,
            }
        except EmpireHTTPError as exc:
            return self._handle_buy_error(exc)
        except Exception as exc:
            return {"status": "failed", "reason": f"购买请求异常: {exc}"}

        # ── 步骤 c: 写入 PurchaseRecord ──
        purchase_id = None
        try:
            async with self.session_factory() as session:
                record = PurchaseRecord(
                    item_id=item_id,
                    market_hash_name=item_name,
                    purchase_price_usd=price_usd,
                    buff_price_usd=buff_price_usd,
                    discount_pct=discount_pct,
                    is_auto=is_auto,
                    account_name=account_name,
                )
                session.add(record)
                await session.commit()
                await session.refresh(record)
                purchase_id = record.id
                logger.info("购买记录已写入: purchase_id=%d", purchase_id)
        except Exception as exc:
            logger.error("写入 PurchaseRecord 失败: %s", exc)

        return {
            "status": "success",
            "purchase_id": purchase_id,
            "item_id": item_id,
            "item_name": item_name,
            "price_usd": price_usd,
            "is_auto": is_auto,
        }

    # ────────────────────────── 出价 ──────────────────────────

    async def place_bid(
        self,
        auction_id: str,
        amount: float,
        auction_name: str,
        discount_at_bid: float,
        account_name: str,
        is_auto: bool = False,
    ) -> dict:
        """执行拍卖出价。

        流程:
          1. 检查余额 → 2. 调用出价 API → 3. 写入 BidRecord

        Args:
            auction_id: 拍卖 ID
            amount: 出价金额（平台币）
            auction_name: 皮肤名
            discount_at_bid: 出价时折扣率
            account_name: 账号名
            is_auto: 是否自动出价

        Returns:
            {"status": "success"|"failed", "bid_id"?: int, "reason"?: str}

        ⚠️ 推测接口：place_auction_bid 的实际路径和参数需抓包确认。
        """
        # ── 步骤 a: 余额检查 ──
        try:
            balance_data = await asyncio.wait_for(
                self.http_client.get_balance(),
                timeout=10.0,
            )
            balance_usd = float(balance_data.get("balance_usd", 0))
            # 出价金额是平台币，估算 USD
            estimated_usd = amount * 0.01  # 粗略估算，实际由调用方提供准确汇率
            required = estimated_usd * BALANCE_BUFFER_RATIO

            if balance_usd < required and balance_usd < LOW_BALANCE_THRESHOLD:
                return {
                    "status": "failed",
                    "reason": f"余额不足: 当前 ${balance_usd:.2f}",
                    "balance": balance_usd,
                }
        except asyncio.TimeoutError:
            logger.warning("余额查询超时")
        except Exception as exc:
            logger.warning("余额查询失败: %s", exc)

        # ── 步骤 b: 执行出价 ──
        try:
            result = await asyncio.wait_for(
                self.http_client.place_auction_bid(auction_id, amount),
                timeout=10.0,
            )
            logger.info("出价已发送: auction=%s, amount=%.1f, discount=%.1f%%",
                        auction_id, amount, discount_at_bid)

        except asyncio.TimeoutError:
            return {"status": "failed", "reason": "出价请求超时"}
        except EmpireHTTPError as exc:
            return self._handle_bid_error(exc)
        except Exception as exc:
            return {"status": "failed", "reason": f"出价请求异常: {exc}"}

        # ── 步骤 c: 写入 BidRecord ──
        bid_id = None
        try:
            async with self.session_factory() as session:
                record = BidRecord(
                    auction_id=auction_id,
                    account_name=account_name,
                    bid_amount=amount,
                    bid_amount_usd=round(amount * 0.01, 4),  # 估算 USD
                    discount_at_bid=round(discount_at_bid, 2),
                    is_auto=is_auto,
                )
                session.add(record)
                await session.commit()
                await session.refresh(record)
                bid_id = record.id
                logger.info("出价记录已写入: bid_id=%d", bid_id)
        except Exception as exc:
            logger.error("写入 BidRecord 失败: %s", exc)

        return {
            "status": "success",
            "bid_id": bid_id,
            "auction_id": auction_id,
            "amount": amount,
            "discount_at_bid": discount_at_bid,
            "is_auto": is_auto,
        }

    # ────────────────────────── 错误处理 ──────────────────────────

    @staticmethod
    def _handle_buy_error(exc: EmpireHTTPError) -> dict:
        """根据 HTTP 错误码返回友好的购买失败消息。"""
        code = exc.status_code or 0
        reasons = {
            429: "请求过快，请稍后重试",
            401: "API Key 失效，请检查账号配置",
            403: "无权限执行此操作",
            404: "物品不存在或已被购买",
            409: "物品已被他人购买",
            410: "物品已下架",
        }
        reason = reasons.get(code, f"购买失败 (HTTP {code})")
        logger.warning("购买失败 [%d]: %s", code, reason)
        return {"status": "failed", "reason": reason}

    @staticmethod
    def _handle_bid_error(exc: EmpireHTTPError) -> dict:
        """根据 HTTP 错误码返回友好的出价失败消息。"""
        code = exc.status_code or 0
        reasons = {
            429: "请求过快，请稍后重试",
            401: "API Key 失效，请检查账号配置",
            403: "无权限参与此拍卖",
            404: "拍卖不存在或已结束",
            409: "出价已被他人超越",
        }
        reason = reasons.get(code, f"出价失败 (HTTP {code})")
        logger.warning("出价失败 [%d]: %s", code, reason)
        return {"status": "failed", "reason": reason}

    # ────────────────────────── 订单跟踪 ──────────────────────────

    def add_pending_order(self, order_id: str, order_info: dict) -> None:
        """添加待确认订单到跟踪队列。"""
        self.pending_orders[order_id] = {
            **order_info,
            "created_at": datetime.now(timezone.utc),
            "status": "pending",
        }
        logger.debug("待确认订单: %s", order_id)

    def confirm_order(self, order_id: str, success: bool, detail: str = "") -> None:
        """确认订单状态。"""
        if order_id in self.pending_orders:
            self.pending_orders[order_id]["status"] = "confirmed" if success else "failed"
            self.pending_orders[order_id]["detail"] = detail
            self.pending_orders[order_id]["confirmed_at"] = datetime.now(timezone.utc)

    def get_pending_orders(self) -> list[dict]:
        """获取所有待确认订单。"""
        return [
            {"id": oid, **info}
            for oid, info in self.pending_orders.items()
            if info.get("status") == "pending"
        ]


# ═══════════════════════════════════════════════════════════════
# 余额监控服务
# ═══════════════════════════════════════════════════════════════

class BalanceMonitor:
    """余额监控器：定时查询余额，低于阈值时告警。

    使用方式:
        monitor = BalanceMonitor(http_client=empire_http, threshold=50.0)
        await monitor.start()
        # ... 获取余额 ...
        balance = monitor.last_balance
        await monitor.stop()
    """

    def __init__(
        self,
        http_client: EmpireHTTPClient,
        threshold: float = LOW_BALANCE_THRESHOLD,
        interval: float = BALANCE_CHECK_INTERVAL,
    ):
        self.http_client = http_client
        self.threshold = threshold
        self.interval = interval
        self.last_balance: Optional[dict] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._alert_callbacks: list = []

    @property
    def is_running(self) -> bool:
        return self._running

    def on_low_balance(self, callback) -> None:
        """注册低余额告警回调。callback(balance_data, threshold)"""
        self._alert_callbacks.append(callback)

    async def start(self) -> None:
        """启动余额监控后台任务。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("余额监控已启动: 间隔=%.0fs, 阈值=$%.0f", self.interval, self.threshold)

    async def stop(self) -> None:
        """停止余额监控。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("余额监控已停止")

    async def fetch_now(self) -> Optional[dict]:
        """立即查询一次余额并更新缓存。"""
        try:
            balance = await asyncio.wait_for(
                self.http_client.get_balance(),
                timeout=10.0,
            )
            self.last_balance = balance
            self._check_threshold(balance)
            return balance
        except asyncio.TimeoutError:
            logger.warning("余额查询超时")
        except Exception as exc:
            logger.warning("余额查询失败: %s", exc)
        return None

    # ────────────────────────── 内部 ──────────────────────────

    async def _monitor_loop(self) -> None:
        """余额监控循环。"""
        await asyncio.sleep(5)  # 启动后先等 5 秒

        while self._running:
            try:
                await self.fetch_now()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("余额监控异常: %s", exc)

            await asyncio.sleep(self.interval)

    def _check_threshold(self, balance: dict) -> None:
        """检查余额是否低于阈值，触发告警。"""
        balance_usd = float(balance.get("balance_usd", 0))

        if balance_usd < self.threshold:
            logger.warning(
                "⚠️ 余额不足: $%.2f < 阈值 $%.2f",
                balance_usd, self.threshold,
            )
            for cb in self._alert_callbacks:
                try:
                    cb(balance, self.threshold)
                except Exception as exc:
                    logger.error("告警回调异常: %s", exc)
