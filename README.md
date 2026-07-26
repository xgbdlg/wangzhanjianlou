# CSGOEmpire 双引擎捡漏助手

P2P 市场 + 拍卖实时监控与自动捡漏工具。后端 Python FastAPI + Chrome 扩展前端。

## 功能概览

| 模块 | 功能 |
|---|---|
| **市场捡漏** | 轮询 Empire P2P 市场 → 对比 Buff 价格 → 折扣达标自动购买 |
| **拍卖捡漏** | 监听 Empire WS 拍卖事件 → 实时竞价 → 止损退出 |
| **价格服务** | 三级缓存（内存→SQLite→cs2.sh API） |
| **多账号** | 并发管理多个 CSGOEmpire 账号，独立策略 |
| **Chrome 插件** | 弹出面板 + 监控仪表盘 + 配置页 + 桌面通知 |
| **统计报表** | 今日/本周/本月 市场+拍卖 聚合统计 |

## 快速开始

### 环境要求

- Python 3.11+
- Chrome 浏览器（用于加载插件）

### 1. 启动后端

**Windows:**
```bash
start.bat
```

**macOS / Linux:**
```bash
chmod +x start.sh
./start.sh
```

**手动启动:**
```bash
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8080
```

启动后访问: http://127.0.0.1:8080/docs （Swagger API 文档）

### 2. 加载 Chrome 插件

1. 打开 `chrome://extensions/`
2. 右上角开启 **开发者模式**
3. 点击 **加载已解压的扩展程序**
4. 选择 `extension/` 目录
5. 固定插件到工具栏

### 3. 首次使用

```
① 打开插件 popup → 如果后端未初始化，点配置页
② 配置页 → 设置主密码 (POST /api/init)
③ 添加 CSGOEmpire 账号 (名称 + API Key + 汇率)
④ 策略设置 → 配置捡漏参数 (折扣阈值/黑白名单/自动购买)
⑤ 回到 popup → 选择账号 → 启动市场/拍卖监控
⑥ 监控面板 → 实时查看捡漏结果
```

## 项目结构

```
网站脚本/
├── backend/                      # 后端 API
│   ├── main.py                   # 入口：FastAPI + 日志 + 异常处理
│   ├── database.py               # SQLite 异步连接
│   ├── models.py                 # 7 个 ORM 模型
│   ├── security.py               # Fernet 加密存储
│   ├── schemas.py                # Pydantic 请求/响应模型
│   ├── routes/                   # API 路由 (9 个模块)
│   │   ├── init.py               #   POST /api/init
│   │   ├── accounts.py           #   账号 CRUD (5 端点)
│   │   ├── strategy.py           #   策略配置 (2 端点)
│   │   ├── prices.py             #   价格查询 (3 端点)
│   │   ├── config.py             #   运行时配置 + 加密导入导出
│   │   ├── empire.py             #   Empire 连接 (4 端点)
│   │   ├── market.py             #   市场监控 (5 端点)
│   │   ├── auction.py            #   拍卖监控 (7 端点)
│   │   └── stats.py              #   统计报表
│   ├── services/                 # 业务逻辑层
│   │   ├── price_fetcher.py      #   三级缓存价格查询
│   │   ├── name_normalizer.py    #   皮肤名称标准化 + 通配符过滤
│   │   ├── empire_http.py        #   Empire HTTP 客户端 (限流+重试)
│   │   ├── empire_ws.py          #   Empire WS 客户端 (Socket.IO)
│   │   ├── market_engine.py      #   市场捡漏引擎
│   │   ├── auction_state.py      #   拍卖状态机
│   │   ├── auction_engine.py     #   拍卖捡漏引擎 (止损+预判出价)
│   │   ├── executor.py           #   交易执行器 + 余额监控
│   │   ├── multi_account.py      #   多账号并发管理
│   │   └── ws_broadcast.py       #   前端 WS 广播
│   ├── test_phase2.py            # Phase 2 集成测试
│   ├── test_phase3.py            # Phase 3 集成测试
│   └── requirements.txt          # Python 依赖
├── extension/                    # Chrome 插件
│   ├── manifest.json             # Manifest V3
│   ├── background.js             # Service Worker (WS + 通知)
│   ├── popup.html/css/js         # 弹出面板
│   ├── options.html/css/js       # 配置页 (3 标签)
│   ├── monitor.html/css/js       # 监控面板 (实时表格 + 日志)
│   └── icons/                    # 插件图标
├── build/
│   └── build_backend.py          # PyInstaller 打包脚本
├── start.bat                     # Windows 启动脚本
├── start.sh                      # macOS/Linux 启动脚本
└── README.md                     # 本文件
```

## API 端点一览 (33 个)

| 分类 | 端点 | 方法 |
|---|---|---|
| 系统 | `/api/health` `/api/init` | GET / POST |
| 账号 | `/api/accounts` `/{name}` `/switch` `/current` | CRUD |
| 策略 | `/api/strategy` | GET / POST |
| 配置 | `/api/config` `/export` `/import` | GET / POST |
| 价格 | `/api/prices` `/batch` `/refresh` | GET / POST |
| Empire | `/api/empire/connect` `/balance` `/disconnect` `/status` | POST / GET |
| 市场 | `/api/market/start` `/stop` `/deals` `/buy` `/status` | POST / GET |
| 拍卖 | `/api/auction/start` `/stop` `/active` `/{id}/bid` `/{id}/abort` `/history` `/status` | POST / GET |
| 统计 | `/api/stats?period=today` | GET |
| 余额 | `/api/balance` | GET |

## 安全说明

- **API Key 加密存储**: 使用 Fernet (PBKDF2-SHA256 + AES) 加密，主密码不落盘
- **本地运行**: 所有数据存储在 `~/.csgoempire-bot/`，不上传云端
- **主密码**: 首次使用时设置，重启后需通过 `X-Master-Password` Header 或 `/api/init` 解锁
- **建议**: 不要与他人共享主密码，定期备份 `~/.csgoempire-bot/`

## 常见问题

**Q: 启动后 Chrome 插件显示"后端未连接"？**
A: 确认后端已启动：访问 http://127.0.0.1:8080/api/health 应返回 `{"status":"ok"}`

**Q: 价格查询返回 503？**
A: 需设置 cs2.sh API Key：插件配置页 → 填入 Key，或设置环境变量 `CS2SH_API_KEY`

**Q: Empire 连接失败？**
A: CSGOEmpire API 未公开文档，部分接口基于推测。需通过浏览器 DevTools 抓包确认实际接口格式后调整代码。

**Q: 如何备份数据？**
A: 插件配置页 → 数据管理 → 导出配置（加密）。或直接复制 `~/.csgoempire-bot/` 目录。

**Q: 多个账号能否同时监控？**
A: 可以。MultiAccountManager 为每个账号创建独立引擎，通过 `asyncio.gather` 并发运行。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端框架 | FastAPI + Uvicorn |
| 数据库 | SQLite + SQLAlchemy 2.0 (async) |
| 加密 | Cryptography (Fernet) |
| HTTP 客户端 | httpx |
| WebSocket | websockets |
| 前端 | Chrome Extension Manifest V3 (原生 JS) |
| 打包 | PyInstaller |

## 免责声明

**本工具仅供学习和研究使用。** 使用本工具进行的任何交易操作，风险由使用者自行承担。开发者不对因使用本工具导致的任何损失负责。

CSGOEmpire 的 API 接口未公开文档，部分实现基于推测和逆向分析，可能随时失效。请在了解风险的前提下使用。

---

*Generated with Claude Code — Phase 1~10 complete*
