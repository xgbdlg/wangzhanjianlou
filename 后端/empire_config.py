"""
CSGOEmpire API 配置文件
========================
所有标记 [待抓包] 的字段需要通过浏览器 DevTools 确认。
抓包方法见文件末尾说明。
"""

# ═══════════════════════════════════════════════════════════
# 1. HTTP API 端点
# ═══════════════════════════════════════════════════════════

EMPIRE_HTTP = {
    # 市场物品列表 — 打开 CSGOEmpire 市场页面，看 Network → XHR
    "get_items": {
        "method": "GET",
        "path": "/api/v2/trading/items",       # [待抓包] 确认实际路径
        "params": {"per_page": 100},
        "response_fields": {                     # [待抓包] 确认响应字段名
            "item_id": "id",                     # Empire 物品ID的字段名
            "name": "market_hash_name",          # 皮肤名的字段名
            "wear": "wear",                      # 磨损的字段名
            "price": "price",                    # 价格的字段名 (Coins)
            "status": "status",                  # 状态的字段名
        },
    },
    # 余额查询 — 刷新页面，看 Network → XHR → balance
    "get_balance": {
        "method": "GET",
        "path": "/api/v2/user/balance",          # [待抓包] 确认实际路径
        "response_fields": {
            "balance": "balance",                # 平台币余额字段名
            "balance_usd": "balance_usd",        # USD 余额字段名
        },
    },
    # 市场购买 — 在市场上点"购买"，看 Network 请求
    "withdraw_item": {
        "method": "POST",
        "path": "/api/v2/trading/withdraw",      # [待抓包] 确认实际路径
        "request_fields": {
            "item_id": "item_id",                # 请求中物品ID的字段名
        },
    },
    # 拍卖出价 — 在拍卖中出价，看 Network 请求
    "place_auction_bid": {
        "method": "POST",
        "path": "/api/v2/trading/auction/bid",   # [待抓包] 确认实际路径
        "request_fields": {
            "auction_id": "auction_id",
            "amount": "amount",
        },
    },
}

# ═══════════════════════════════════════════════════════════
# 2. WebSocket 配置
# ═══════════════════════════════════════════════════════════

EMPIRE_WS = {
    # 连接地址 — 打开 DevTools → Network → WS 标签
    "url": "wss://csgoempire.com/socket.io/?EIO=3&transport=websocket",  # [待抓包] 确认

    # 认证消息 — 连接成功后发的第一条 42[...] 消息
    "auth": {
        "event": "identify",                     # [待抓包] 认证事件名，可能是 "auth"/"login"
        "payload": {                             # [待抓包] 认证参数
            "uid": "",                           # 用户ID，可能需要先获取
            "token": "",                         # API Key 或 Token
        },
    },

    # 事件名映射 — 从 WS 消息中提取的事件名
    "events": {
        "auction_started": "auction_started",    # [待抓包] 拍卖开始事件
        "auction_bid": "auction_bid",            # [待抓包] 有人出价事件
        "auction_won": "auction_won",            # [待抓包] 拍卖中标事件
        "auction_expired": "auction_expired",    # [待抓包] 拍卖过期事件
        "new_item": "new_item",                  # [待抓包] 新物品上架
        "item_sold": "item_sold",                # [待抓包] 物品售出
    },

    # 事件数据字段名 — 每个事件中数据的字段名
    "event_fields": {
        "auction_started": {
            "auction_id": "id",                  # [待抓包]
            "name": "market_hash_name",          # [待抓包]
            "wear": "wear",                      # [待抓包]
            "base_price": "base_price",          # [待抓包]
            "starting_bid": "starting_bid",      # [待抓包]
        },
        "auction_bid": {
            "auction_id": "auction_id",          # [待抓包]
            "new_bid": "new_bid",                # [待抓包]
            "bidder_name": "bidder_name",        # [待抓包]
        },
        "auction_won": {
            "auction_id": "auction_id",          # [待抓包]
            "winner_name": "winner_name",        # [待抓包]
            "final_bid": "final_bid",            # [待抓包]
        },
    },
}

# ═══════════════════════════════════════════════════════════
# 3. 物品状态值
# ═══════════════════════════════════════════════════════════

# [待抓包] Empire 返回的 status 字段可能值
ACTIVE_STATUSES = ["active", "available", "listed"]

# ═══════════════════════════════════════════════════════════
# 抓包步骤
# ═══════════════════════════════════════════════════════════
"""
1. 打开 Chrome，登录 CSGOEmpire
2. 按 F12 打开 DevTools
3. 切换到 Network（网络）标签
4. 勾选 "Preserve log"（保留日志）

【抓 HTTP API】
5. 浏览市场页面，观察 Network 中出现的 XHR/Fetch 请求
6. 找到物品列表请求 → 记录 URL、参数、响应 JSON 结构
7. 点击购买按钮 → 记录请求 URL、请求体、响应
8. 进入拍卖页面 → 重复以上步骤
9. 把上面 EMPIRE_HTTP 中的 [待抓包] 字段更新为实际值

【抓 WebSocket】
10. 切换到 WS 标签（或 Network → 筛选 WS）
11. 点击 WebSocket 连接 → Messages 标签
12. 观察连接后服务器发的第一条消息（握手/认证相关）
13. 在拍卖页面操作，观察收到的消息格式：
    - 拍卖开始时收到的消息 → 记录事件名和数据字段
    - 有人出价时收到的消息
    - 拍卖结束时收到的消息
14. 把上面 EMPIRE_WS 中的 [待抓包] 字段更新为实际值

【修改代码】
15. 根据抓包结果修改：
    - services/empire_http.py → 更新 HTTP 路径和响应解析
    - services/empire_ws.py → 更新 WS 认证和事件名
    - services/auction_engine.py → 更新事件数据字段名
"""
