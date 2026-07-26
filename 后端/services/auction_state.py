# services/auction_state.py
# 拍卖状态机：定义拍卖生命周期和状态转换规则
#
# 状态流转图:
#                         auction_started
#                              |
#              +---------------+---------------+
#              v               v               v
#         BIDDING          WAITING          (忽略)
#       折扣>=min_deal  max_loss<=折扣    折扣<max_loss
#              |            <min_deal           |
#              |               |                |
#              +-------+-------+                |
#              v       v       v                |
#         WON   EXPIRED   ABORTED <-------------+
#        (自己赢)(3min超时)(跌破止损)

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

# 拍卖持续时间（秒）
AUCTION_DURATION = 180  # 3 分钟


class AuctionStatus(str, Enum):
    """拍卖状态枚举。

    MONITORING  → 初始监控中，价格合适但未开始竞价
    BIDDING     → 正在竞价（折扣 ≥ min_deal_pct）
    WAITING     → 观望中（折扣在 max_loss 和 min_deal 之间，暂不出价）
    ABORTED     → 止损退出（折扣跌破 max_loss_pct，放弃竞拍）
    WON         → 竞拍成功（自己中标）
    EXPIRED     → 拍卖过期（3 分钟到，未中标）
    """
    MONITORING = "monitoring"
    BIDDING = "bidding"
    WAITING = "waiting"
    ABORTED = "aborted"
    WON = "won"
    EXPIRED = "expired"


@dataclass
class AuctionItem:
    """拍卖物品数据类，跟踪单个拍卖的完整生命周期。

    属性:
        id: Empire 拍卖 ID
        market_hash_name: 皮肤市场名
        wear: 磨损等级
        base_price: Empire 基准价（平台币）
        starting_bid: 起拍价（平台币）
        current_bid: 当前最高出价（平台币），随拍卖更新
        buff_price_usd: Buff 参考价（USD）
        status: 当前状态（见 AuctionStatus）
        account_name: 关联账号
        started_at: 拍卖开始时间
        expires_at: 拍卖过期时间（started_at + 3分钟）
        max_discount_pct: 达到过的最大折扣（用于记录）
        bid_count: 本人出价次数
    """
    id: str
    market_hash_name: str
    wear: str
    base_price: float
    starting_bid: float
    current_bid: float
    buff_price_usd: float
    status: AuctionStatus = AuctionStatus.MONITORING
    account_name: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    max_discount_pct: float = 0.0
    bid_count: int = 0

    def __post_init__(self):
        if self.expires_at is None:
            self.expires_at = self.started_at + timedelta(seconds=AUCTION_DURATION)

    # ────────────────────────── 核心计算方法 ──────────────────────────

    def calculate_discount(self, empire_rate: float) -> float:
        """计算当前出价对应的折扣率。

        公式: discount = (buff_price_usd - current_bid × empire_rate) / buff_price_usd × 100

        Args:
            empire_rate: Empire 平台币 → USD 汇率

        Returns:
            float: 折扣百分比。正数 = 有折扣（Empire 比 Buff 便宜），负数 = 溢价

        >>> item = AuctionItem(id='1', name='AK-47 | Redline', wear='FT',
        ...                    base_price=10, starting_bid=5, current_bid=7,
        ...                    buff_price_usd=6.90)
        >>> item.calculate_discount(0.65)
        34.1  # (6.90 - 7×0.65) / 6.90 × 100
        """
        if self.buff_price_usd <= 0:
            return -999.0  # 无 Buff 价格时返回极低折扣
        current_price_usd = self.current_bid * empire_rate
        return (self.buff_price_usd - current_price_usd) / self.buff_price_usd * 100.0

    def predict_next_discount(self, empire_rate: float) -> float:
        """预判下一次出价后的折扣率。

        出价策略: next_bid = current_bid × 1.01（加价 1%）
        公式: discount = (buff - next_bid × rate) / buff × 100

        用于出价前检查：如果预判折扣 < max_loss_pct，则放弃出价。
        """
        next_bid = self.current_bid * 1.01
        if self.buff_price_usd <= 0:
            return -999.0
        next_price_usd = next_bid * empire_rate
        return (self.buff_price_usd - next_price_usd) / self.buff_price_usd * 100.0

    def is_expired(self) -> bool:
        """检查拍卖是否已过期（超过 3 分钟）。"""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    # ────────────────────────── 状态转换方法 ──────────────────────────

    def transition_to(self, new_status: AuctionStatus, reason: str = "") -> None:
        """执行状态转换并记录日志。

        允许的转换（状态机约束）:
          MONITORING → BIDDING | WAITING
          BIDDING    → WAITING | ABORTED | WON | EXPIRED
          WAITING    → BIDDING | ABORTED | EXPIRED
          终态 (WON/ABORTED/EXPIRED) 不可再转换
        """
        old_status = self.status

        # 终态检查
        if old_status in (AuctionStatus.WON, AuctionStatus.ABORTED, AuctionStatus.EXPIRED):
            raise ValueError(
                f"拍卖 {self.id} 已处于终态 {old_status.value}，"
                f"不能再转换到 {new_status.value}"
            )

        self.status = new_status
        if reason:
            import logging
            logger = logging.getLogger("auction_state")
            logger.info(
                "拍卖状态转换: %s %s → %s (%s)",
                self.id, old_status.value, new_status.value, reason,
            )

    def to_dict(self) -> dict:
        """转为字典，便于 JSON 序列化和前端展示。"""
        return {
            "id": self.id,
            "market_hash_name": self.market_hash_name,
            "wear": self.wear,
            "base_price": self.base_price,
            "current_bid": self.current_bid,
            "buff_price_usd": round(self.buff_price_usd, 4),
            "discount_pct": round(
                self.calculate_discount(self.base_price / self.starting_bid if self.starting_bid > 0 else 0.65),
                2,
            ),
            "status": self.status.value,
            "account_name": self.account_name,
            "started_at": self.started_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "bid_count": self.bid_count,
        }


# ────────────────────────── 辅助函数 ──────────────────────────

def make_auction_item(
    auction_id: str,
    market_hash_name: str,
    wear: str,
    base_price: float,
    starting_bid: float,
    buff_price_usd: float,
    account_name: str = "",
) -> AuctionItem:
    """工厂函数：从拍卖开始事件创建 AuctionItem。

    Args:
        auction_id: Empire 拍卖 ID
        market_hash_name: 标准化后的皮肤名
        wear: 磨损等级
        base_price: 基准价（平台币）
        starting_bid: 起拍价
        buff_price_usd: Buff 参考价（USD）
        account_name: 关联账号名

    Returns:
        初始状态为 MONITORING 的 AuctionItem
    """
    now = datetime.now(timezone.utc)
    return AuctionItem(
        id=auction_id,
        market_hash_name=market_hash_name,
        wear=wear,
        base_price=base_price,
        starting_bid=starting_bid,
        current_bid=starting_bid,  # 初始当前价 = 起拍价
        buff_price_usd=buff_price_usd,
        status=AuctionStatus.MONITORING,
        account_name=account_name,
        started_at=now,
        expires_at=now + timedelta(seconds=AUCTION_DURATION),
    )
