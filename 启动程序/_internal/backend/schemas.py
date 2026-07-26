from typing import Optional

from pydantic import BaseModel


class StatusResponse(BaseModel):
    status: str


class HealthResponse(BaseModel):
    status: str
    path_exists: bool


class InitRequest(BaseModel):
    """初始化请求体模型，接收主密码。"""
    master_password: str


class AccountCreateRequest(BaseModel):
    """创建账号请求体模型。"""
    name: str
    api_key: str
    empire_rate: float


class AccountResponse(BaseModel):
    """账号返回信息，不包含敏感字段。"""
    name: str
    empire_rate: float
    created_at: Optional[str] = None


class AccountCreateResponse(BaseModel):
    status: str
    account: AccountResponse


class AccountListResponse(BaseModel):
    accounts: list[AccountResponse]


class AccountSwitchResponse(BaseModel):
    status: str
    current_account: str


class CurrentAccountResponse(BaseModel):
    current_account: Optional[str] = None


class StrategyRequest(BaseModel):
    """策略配置请求体。"""
    account_name: Optional[str] = None
    buff_rate: float
    min_deal_pct: float
    max_loss_pct: float
    auto_bid: bool
    auto_buy: bool
    max_bid_usd: float
    max_buy_usd: float
    min_item_price: float
    max_item_price: float
    whitelist: str
    blacklist: str
    wear_filter: str
    bid_delay_ms: int


class StrategyResponse(BaseModel):
    """策略配置返回值。"""
    buff_rate: float
    min_deal_pct: float
    max_loss_pct: float
    auto_bid: bool
    auto_buy: bool
    max_bid_usd: float
    max_buy_usd: float
    min_item_price: float
    max_item_price: float
    whitelist: str
    blacklist: str
    wear_filter: str
    bid_delay_ms: int


# ────────────────────────── Phase 3: 价格查询 ──────────────────────────

class PriceItem(BaseModel):
    """单个物品价格信息。"""
    ask: float = 0.0
    bid: float = 0.0
    ask_volume: int = 0
    updated_at: Optional[str] = None
    error: Optional[str] = None


class SinglePriceResponse(BaseModel):
    """单物品价格查询响应。"""
    data: dict[str, PriceItem]


class BatchPriceRequest(BaseModel):
    """批量价格查询请求体。"""
    items: list[str]  # 最多 100 个
    source: str = "buff"


class BatchPriceResponse(BaseModel):
    """批量价格查询响应。"""
    data: dict[str, PriceItem]


# ────────────────────────── Phase 4: Empire 连接 ──────────────────────────

class EmpireConnectResponse(BaseModel):
    """Empire 连接响应。"""
    status: str
    account: str
    balance: Optional[dict] = None
    ws_connected: bool = False
    warning: Optional[str] = None


class EmpireBalanceResponse(BaseModel):
    """Empire 余额响应。"""
    account: str
    balance: dict


class EmpireStatusResponse(BaseModel):
    """Empire 连接状态响应。"""
    status: str
    account: Optional[str] = None
    http_connected: bool = False
    ws_connected: bool = False


# ────────────────────────── Phase 5: 市场捡漏 ──────────────────────────

class MarketDealItem(BaseModel):
    """市场捡漏记录条目。"""
    id: int
    item_id: str
    market_hash_name: str
    wear: Optional[str] = None
    empire_price_usd: float
    buff_price_usd: float
    discount_pct: float
    status: str
    account_name: str
    created_at: Optional[str] = None
    bought_at: Optional[str] = None


class MarketDealsResponse(BaseModel):
    """捡漏记录列表响应。"""
    deals: list[MarketDealItem]
    total: int


class MarketStartResponse(BaseModel):
    """市场引擎启动响应。"""
    status: str
    account: Optional[str] = None
    stats: Optional[dict] = None
    warning: Optional[str] = None


class MarketStatsResponse(BaseModel):
    """捡漏引擎状态响应。"""
    status: str
    account: Optional[str] = None
    stats: Optional[dict] = None
