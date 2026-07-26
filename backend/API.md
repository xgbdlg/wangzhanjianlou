# CSGOEmpire 双引擎捡漏助手 — API 接口文档

> 自动生成于 2026-07-26 | 版本 1.0.0 | Base URL: `http://127.0.0.1:8080`

---

## 概览

| 分类 | 端点数量 |
|---|---|
| 健康检查 | 1 |
| 系统初始化 | 1 |
| 账号管理 | 5 |
| 策略配置 | 2 |
| **合计** | **9** |

所有响应均为 `application/json`。带尾部斜杠的变体路径（如 `/api/health/`）功能等价，但已从 OpenAPI Schema 中排除。

---

## 1. 健康检查

### `GET /api/health`

检查服务运行状态及数据目录是否存在。

**请求体：** 无

**响应** `200 OK`

```json
{
  "status": "ok",
  "path_exists": true
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | `string` | 固定为 `"ok"` |
| `path_exists` | `bool` | `~/.csgoempire-bot/` 目录是否存在 |

---

## 2. 系统初始化

### `POST /api/init`

初始化整个系统：创建数据目录 → 初始化安全存储 → 创建数据库表 → 插入默认全局策略。

> **必须最先调用**，否则其他接口会因为 storage/db 未初始化而返回错误。

**请求体**

```json
{
  "master_password": "your-master-password"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `master_password` | `string` | ✅ | 主密码，用于派生加密密钥（PBKDF2-SHA256） |

**响应** `200 OK`

```json
{
  "status": "ok"
}
```

**错误**

| 状态码 | 说明 |
|---|---|
| `500` | 无法创建数据目录 |

---

## 3. 账号管理

### 3.1 创建账号 — `POST /api/accounts`

同时写入主数据库（`data.db`）和加密存储（`accounts.db`）。

**请求头**

| Header | 必填 | 说明 |
|---|---|---|
| `Content-Type` | ✅ | `application/json` |
| `X-Master-Password` | 否 | 若未调用 `/api/init`，可用此 Header 传入主密码以初始化 storage |

**请求体**

```json
{
  "name": "账号A",
  "api_key": "your-csgoempire-api-key",
  "empire_rate": 0.65
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `string` | ✅ | 账号名（唯一，最大 128 字符） |
| `api_key` | `string` | ✅ | CSGOEmpire API Key（加密存储） |
| `empire_rate` | `float` | ✅ | Empire 汇率（如 0.65 = 1 USD → 0.65 平台币） |

**响应** `200 OK`

```json
{
  "status": "ok",
  "account": {
    "name": "账号A",
    "empire_rate": 0.65
  }
}
```

**错误**

| 状态码 | 说明 |
|---|---|
| `400` | 账号已存在 / Secure storage 未初始化 |
| `500` | 账号保存失败 |

---

### 3.2 列出所有账号 — `GET /api/accounts`

返回所有账号，**不含敏感 API Key**。

**请求体：** 无

**响应** `200 OK`

```json
{
  "accounts": [
    {
      "name": "账号A",
      "empire_rate": 0.65,
      "created_at": "2026-07-26T12:00:00"
    }
  ]
}
```

---

### 3.3 切换当前账号 — `POST /api/accounts/{name}/switch`

将当前活跃账号切换为指定账号。

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | `string` | 账号名（需 URL 编码，如 `%E8%B4%A6%E5%8F%B7A`） |

**响应** `200 OK`

```json
{
  "status": "ok",
  "current_account": "账号A"
}
```

**错误**

| 状态码 | 说明 |
|---|---|
| `404` | 账号不存在 |

---

### 3.4 获取当前账号 — `GET /api/accounts/current`

返回当前活跃账号名（未切换过则为 `null`）。

**请求体：** 无

**响应** `200 OK`

```json
{
  "current_account": "账号A"
}
```

或

```json
{
  "current_account": null
}
```

---

### 3.5 删除账号 — `DELETE /api/accounts/{name}`

同时从主数据库和加密存储中删除账号。若删除的是当前活跃账号，则重置当前账号为 `null`。

**路径参数**

| 参数 | 类型 | 说明 |
|---|---|---|
| `name` | `string` | 账号名（URL 编码） |

**请求头**

| Header | 必填 | 说明 |
|---|---|---|
| `X-Master-Password` | 条件 | 若 storage 未初始化则必填 |

**响应** `200 OK`

```json
{
  "status": "ok"
}
```

**错误**

| 状态码 | 说明 |
|---|---|
| `400` | Secure storage 未初始化 |
| `404` | 账号不存在 |
| `500` | 删除失败 |

---

## 4. 策略配置

### 4.1 读取策略 — `GET /api/strategy`

读取账号专属策略或全局默认策略。

**查询参数**

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `account_name` | `string` | 否 | 传入则查该账号专属策略；不传则查全局策略 |

**响应** `200 OK`

```json
{
  "buff_rate": 0.138,
  "min_deal_pct": 15.0,
  "max_loss_pct": -5.0,
  "auto_bid": true,
  "auto_buy": false,
  "max_bid_usd": 500.0,
  "max_buy_usd": 500.0,
  "min_item_price": 5.0,
  "max_item_price": 2000.0,
  "whitelist": "[]",
  "blacklist": "[]",
  "wear_filter": "[]",
  "bid_delay_ms": 500
}
```

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `buff_rate` | `float` | `0.138` | BUFF 参考汇率 |
| `min_deal_pct` | `float` | `15.0` | 最小成交折扣 % |
| `max_loss_pct` | `float` | `-5.0` | 最大亏损 %（负数 = 不允许亏） |
| `auto_bid` | `bool` | `false` | 是否自动出价（拍卖） |
| `auto_buy` | `bool` | `false` | 是否自动购买（市场） |
| `max_bid_usd` | `float` | `500.0` | 拍卖单次最高出价（USD） |
| `max_buy_usd` | `float` | `500.0` | 市场单次最高购买（USD） |
| `min_item_price` | `float` | `5.0` | 物品最低价格过滤（USD） |
| `max_item_price` | `float` | `2000.0` | 物品最高价格过滤（USD） |
| `whitelist` | `string` | `"[]"` | 物品白名单（JSON 数组字符串） |
| `blacklist` | `string` | `"[]"` | 物品黑名单（JSON 数组字符串） |
| `wear_filter` | `string` | `"[]"` | 磨损度过滤（JSON 数组字符串） |
| `bid_delay_ms` | `int` | `500` | 出价延迟（毫秒） |

**错误**

| 状态码 | 说明 |
|---|---|
| `404` | 账号不存在 / 未找到全局策略配置 |

---

### 4.2 保存策略 — `POST /api/strategy`

保存或更新策略。如果 `account_name` 为空，则更新全局策略；否则为该账号创建/更新专属策略。

**请求体**

```json
{
  "account_name": "账号A",
  "buff_rate": 0.138,
  "min_deal_pct": 15.0,
  "max_loss_pct": -5.0,
  "auto_bid": true,
  "auto_buy": false,
  "max_bid_usd": 500.0,
  "max_buy_usd": 500.0,
  "min_item_price": 5.0,
  "max_item_price": 2000.0,
  "whitelist": "[]",
  "blacklist": "[]",
  "wear_filter": "[]",
  "bid_delay_ms": 500
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `account_name` | `string?` | 否 | `null` 或不传 = 更新全局策略 |
| `buff_rate` | `float` | ✅ | BUFF 参考汇率 |
| `min_deal_pct` | `float` | ✅ | 最小成交折扣 % |
| `max_loss_pct` | `float` | ✅ | 最大亏损 % |
| `auto_bid` | `bool` | ✅ | 自动出价开关 |
| `auto_buy` | `bool` | ✅ | 自动购买开关 |
| `max_bid_usd` | `float` | ✅ | 拍卖最高出价 |
| `max_buy_usd` | `float` | ✅ | 市场最高购买 |
| `min_item_price` | `float` | ✅ | 最低物品价格 |
| `max_item_price` | `float` | ✅ | 最高物品价格 |
| `whitelist` | `string` | ✅ | 白名单 JSON 数组 |
| `blacklist` | `string` | ✅ | 黑名单 JSON 数组 |
| `wear_filter` | `string` | ✅ | 磨损过滤 JSON 数组 |
| `bid_delay_ms` | `int` | ✅ | 出价延迟(ms) |

**响应** `200 OK`

```json
{
  "status": "ok"
}
```

**错误**

| 状态码 | 说明 |
|---|---|
| `404` | `account_name` 对应的账号不存在 |

---

## 附录

### A. 数据库结构

| 数据库 | 位置 | 引擎 | 用途 |
|---|---|---|---|
| `data.db` | `~/.csgoempire-bot/data.db` | SQLite + aiosqlite (异步) | 主业务数据（账号、策略、交易记录） |
| `accounts.db` | `~/.csgoempire-bot/accounts.db` | SQLite (同步) | 加密存储 API Key |

### B. 数据表

| 表名 | 说明 |
|---|---|
| `accounts` | CSGOEmpire 账号信息 |
| `strategy_configs` | 策略配置（全局 + 按账号） |
| `price_cache` | BUFF 市场价格缓存 |
| `market_deals` | 市场捡漏检测记录 |
| `auction_deals` | 拍卖监控与竞价记录 |
| `bid_records` | 拍卖出价历史 |
| `purchase_records` | 市场购买历史 |

### C. 安全模型

- 主密码通过 **PBKDF2-SHA256**（10 万次迭代 + 固定 Salt）派生 **Fernet** 对称密钥
- API Key 使用 Fernet 加密后以 `LargeBinary` 存入 `accounts.db`
- 运行时可通过 `X-Master-Password` 请求头传递主密码以初始化 storage

### D. 调用流程建议

```
POST /api/init          → 初始化系统
POST /api/accounts      → 创建账号（可多次）
POST /api/accounts/{name}/switch  → 切换到目标账号
POST /api/strategy      → 配置该账号策略
GET  /api/strategy?account_name=xxx → 验证策略
```

### E. CORS 配置

允许来源：`chrome-extension://*`、`http://localhost`
