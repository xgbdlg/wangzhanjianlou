"""
CSGOEmpire API 配置文件
========================
数据来源: github.com/OfficialCSGOEmpire/API-Docs
Base URL: https://csgoempire.com/api/v2
认证方式: Authorization: Bearer {API_KEY}
频率限制: 120 请求/60秒
"""

# ═══════════════════════════════════════════════════════════
# 1. HTTP API 端点 (已根据官方文档填写)
# ═══════════════════════════════════════════════════════════

EMPIRE_HTTP = {
    # 市场物品列表 — 来源 GET /api/v2/trades（返回 deposits + withdrawals）
    "get_items": {
        "method": "GET",
        "path": "/api/v2/trades",
        "params": {"per_page": 100},
        # 响应中取 .withdrawals[] 数组，每个元素含以下字段
        "response_fields": {
            "list_key": "withdrawals",     # 物品列表在响应中的 key
            "item_id": "id",
            "name": "market_hash_name",
            "wear": "wear",
            "price": "price",              # 平台币价格
            "status": "status",
        },
    },
    # 余额查询
    "get_balance": {
        "method": "GET",
        "path": "/api/v2/metadata/socket",  # 此接口同时返回余额+WS认证信息
        "response_fields": {
            "balance": "balance",
            "balance_usd": "balance_usd",
            # WS 认证也在同个响应里
            "socket_token": "socket_token",
            "socket_signature": "socket_signature",
        },
    },
    # 市场购买（withdrawal = 从市场取回/购买物品）
    "withdraw_item": {
        "method": "POST",
        "path": "/api/v2/trades/withdraw",
        "request_fields": {
            "item_id": "item_id",
        },
    },
    # 拍卖出价
    "place_auction_bid": {
        "method": "POST",
        "path": "/api/v2/trades/bid",
        "request_fields": {
            "auction_id": "auction_id",
            "amount": "amount",             # 出价金额（平台币）
        },
    },
    # [新增] 获取 WS 认证凭证
    "get_socket_meta": {
        "method": "GET",
        "path": "/api/v2/metadata/socket",
    },
}

# ═══════════════════════════════════════════════════════════
# 2. WebSocket 配置 (已根据官方文档填写)
# ═══════════════════════════════════════════════════════════

EMPIRE_WS = {
    # 交易 WebSocket 地址（Socket.IO v4）
    "url": "wss://trade.csgoempire.com/trade",

    # 认证方式: 先调 /api/v2/metadata/socket 获取 token + signature
    # 然后 WS 连接时传入这两个值
    "auth": {
        "event": "authenticate",
        "payload": {
            "token": "",          # 来自 metadata/socket 响应的 socket_token
            "signature": "",      # 来自 metadata/socket 响应的 socket_signature
        },
    },

    # 事件名 (官方文档确认)
    "events": {
        "new_item": "new_item",           # 新物品上架
        "updated_item": "updated_item",   # 物品信息更新（价格/状态变化）
        "auction_update": "auction_update",  # 拍卖更新（出价/状态变化）
        "deleted_item": "deleted_item",   # 物品下架/售出
        "trade_status": "trade_status",   # 交易状态变化
        "deposit_failed": "deposit_failed",  # 存入失败
        "timesync": "timesync",           # 时间同步
        # 以下为拍卖相关 (auction_update 事件的子类型)
        "auction_started": "auction_update",
        "auction_bid": "auction_update",
        "auction_won": "trade_status",
        "auction_expired": "deleted_item",
    },

    # 事件数据字段 (需实际验证)
    "event_fields": {
        "new_item": {
            "auction_id": "id",
            "name": "market_hash_name",
            "wear": "wear",
            "base_price": "base_price",
            "starting_bid": "starting_bid",
        },
        "auction_update": {
            "auction_id": "id",
            "new_bid": "highest_bid",
            "bidder_name": "bidder_name",
        },
        "trade_status": {
            "auction_id": "id",
            "winner_name": "winner_name",
            "final_bid": "final_bid",
        },
    },
}

# ═══════════════════════════════════════════════════════════
# 3. 物品状态值
# ═══════════════════════════════════════════════════════════

ACTIVE_STATUSES = ["active", "available", "listed"]

# ═══════════════════════════════════════════════════════════
# 4. 待确认项（需实际测试验证）
# ═══════════════════════════════════════════════════════════
"""
以下内容根据 npm 包 csgoempire-wrapper 和官方文档推断，需用真实 Key 测试:

[需验证] withdraw_item 的路径可能为:
         POST /api/v2/trades/withdraw  (创建取回/购买)

[需验证] place_auction_bid 的路径可能为:
         POST /api/v2/trades/bid       (拍卖出价)

[需验证] WS 认证格式，实际可能是:
         42["authenticate", {"token": "xxx", "signature": "xxx"}]

[需验证] auction_update 事件的详细数据结构:
         {"id": "...", "highest_bid": 1234, "bidder_name": "xxx"}

[需验证] 市场购买成功后物品从 withdrawals 数组消失，
         同时 WS 收到 deleted_item 事件

如有问题，用浏览器 DevTools → Network 验证实际请求。
"""
