# models.py
# 定义 SQLAlchemy ORM 模型：账号、策略、价格缓存、市场记录、拍卖记录、出价记录和购买记录

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from database import Base


class Account(Base):
    """存储 CSGOEmpire 账号信息和加密 API Key。"""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    api_key_encrypted = Column(LargeBinary, nullable=False)
    empire_rate = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    strategy_configs = relationship("StrategyConfig", back_populates="account")


class StrategyConfig(Base):
    """存储全局或账号级别的策略配置。"""
    __tablename__ = "strategy_configs"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    is_global = Column(Boolean, default=True, nullable=False)
    buff_rate = Column(Float, default=0.138, nullable=False)
    min_deal_pct = Column(Float, default=15.0, nullable=False)
    max_loss_pct = Column(Float, default=-5.0, nullable=False)
    auto_bid = Column(Boolean, default=False, nullable=False)
    auto_buy = Column(Boolean, default=False, nullable=False)
    max_bid_usd = Column(Float, default=500.0, nullable=False)
    max_buy_usd = Column(Float, default=500.0, nullable=False)
    min_item_price = Column(Float, default=5.0, nullable=False)
    max_item_price = Column(Float, default=2000.0, nullable=False)
    whitelist = Column(Text, default="[]", nullable=False)
    blacklist = Column(Text, default="[]", nullable=False)
    wear_filter = Column(Text, default="[]", nullable=False)
    bid_delay_ms = Column(Integer, default=500, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    account = relationship("Account", back_populates="strategy_configs")


class PriceCache(Base):
    """缓存市场价格信息。"""
    __tablename__ = "price_cache"

    market_hash_name = Column(String(256), primary_key=True, index=True)
    buff_ask = Column(Float, nullable=True)
    buff_bid = Column(Float, nullable=True)
    source = Column(String(128), nullable=True)
    cached_at = Column(DateTime(timezone=True), server_default=func.now())


class MarketDeal(Base):
    """记录市场捡漏检测与购买事件。"""
    __tablename__ = "market_deals"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(String(128), nullable=False)
    market_hash_name = Column(String(256), nullable=False)
    wear = Column(String(64), nullable=True)
    empire_price_usd = Column(Float, nullable=False)
    buff_price_usd = Column(Float, nullable=False)
    discount_pct = Column(Float, nullable=False)
    status = Column(String(64), nullable=False)
    account_name = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    bought_at = Column(DateTime(timezone=True), nullable=True)


class AuctionDeal(Base):
    """记录拍卖监控与竞价状态。"""
    __tablename__ = "auction_deals"

    id = Column(String(128), primary_key=True, index=True)
    market_hash_name = Column(String(256), nullable=False)
    wear = Column(String(64), nullable=True)
    base_price = Column(Float, nullable=False)
    starting_bid = Column(Float, nullable=False)
    final_bid = Column(Float, nullable=True)
    buff_price_usd = Column(Float, nullable=False)
    max_discount_pct = Column(Float, nullable=False)
    status = Column(String(64), nullable=False)
    won_by_me = Column(Boolean, default=False, nullable=False)
    account_name = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)


class BidRecord(Base):
    """记录拍卖出价历史。"""
    __tablename__ = "bid_records"

    id = Column(Integer, primary_key=True, index=True)
    auction_id = Column(String(128), ForeignKey("auction_deals.id"), nullable=False)
    account_name = Column(String(128), nullable=False)
    bid_amount = Column(Float, nullable=False)
    bid_amount_usd = Column(Float, nullable=False)
    discount_at_bid = Column(Float, nullable=False)
    is_auto = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PurchaseRecord(Base):
    """记录市场购买历史。"""
    __tablename__ = "purchase_records"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(String(128), nullable=False)
    market_hash_name = Column(String(256), nullable=False)
    purchase_price_usd = Column(Float, nullable=False)
    buff_price_usd = Column(Float, nullable=False)
    discount_pct = Column(Float, nullable=False)
    is_auto = Column(Boolean, default=False, nullable=False)
    account_name = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
