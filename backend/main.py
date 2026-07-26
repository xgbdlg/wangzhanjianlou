# main.py
# CSGOEmpire 双引擎捡漏助手 — 后端入口
# 启动: python main.py  或  uvicorn main:app --host 127.0.0.1 --port 8080

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from database import AsyncSessionLocal, init_db
from routes import router as api_router
from routes.accounts import router as accounts_router
from routes.config import router as config_router
from routes.auction import router as auction_router
from routes.empire import router as empire_router
from routes.market import router as market_router
from routes.prices import router as prices_router
from routes.stats import router as stats_router
from routes.strategy import router as strategy_router
from schemas import HealthResponse
from services.executor import BalanceMonitor, TradeExecutor
from services.price_fetcher import CS2PriceFetcher
from services.ws_broadcast import ws_handler as frontend_ws_handler

# ═══════════════════════════════════════════════════════════════
# 日志系统配置
# ═══════════════════════════════════════════════════════════════

LOG_DIR = Path.home() / ".csgoempire-bot"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 5

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

# 控制台 Handler (INFO+)
console = logging.StreamHandler(sys.stdout)
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
root_logger.addHandler(console)

# 文件 Handler (DEBUG+)
file_handler = RotatingFileHandler(
    str(LOG_FILE), maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
root_logger.addHandler(file_handler)

logger = logging.getLogger("main")

# ═══════════════════════════════════════════════════════════════
# FastAPI 应用
# ═══════════════════════════════════════════════════════════════

CONFIG_FILE = LOG_DIR / "config.json"

app = FastAPI(
    title="CSGOEmpire 双引擎捡漏助手",
    description="P2P 市场 + 拍卖实时监控与自动捡漏后端",
    version="1.0.0",
    redirect_slashes=False,
)

origins = ["chrome-extension://*", "http://localhost"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ────────────────────────── 全局异常处理 ──────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """全局异常处理器：捕获所有未处理异常，返回友好错误。"""
    logger.error("未处理异常 [%s %s]: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "服务器内部错误", "detail": str(exc)[:200]},
    )


@app.exception_handler(503)
async def service_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"status": "error", "message": "服务暂不可用，请稍后重试"},
    )


# ────────────────────────── 配置持久化 ──────────────────────────

def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _get_api_key() -> str:
    env_key = os.getenv("CS2SH_API_KEY", "")
    if env_key:
        return env_key
    return _load_config().get("cs2sh_api_key", "")


def _init_price_fetcher(app: FastAPI) -> CS2PriceFetcher | None:
    api_key = _get_api_key()
    if not api_key:
        logger.warning("未配置 cs2.sh API Key，价格查询服务不可用（可通过 POST /api/config 设置）")
        return None
    fetcher = CS2PriceFetcher(api_key=api_key, session_factory=AsyncSessionLocal)
    app.state.price_fetcher = fetcher
    logger.info("价格服务已初始化 (cs2.sh)")
    return fetcher


def _start_cleanup_task(fetcher: CS2PriceFetcher) -> asyncio.Task:
    return asyncio.create_task(_cache_cleanup_loop(fetcher))


# ────────────────────────── 后台缓存清理 ──────────────────────────

async def _cache_cleanup_loop(fetcher: CS2PriceFetcher):
    L1_INTERVAL, L2_INTERVAL = 600, 3600
    l2_counter = 0
    await asyncio.sleep(30)
    while True:
        try:
            await fetcher.cleanup_memory_cache()
            l2_counter += L1_INTERVAL
            if l2_counter >= L2_INTERVAL:
                await fetcher.cleanup_sqlite_cache()
                l2_counter = 0
        except Exception as exc:
            logger.error("缓存清理任务异常: %s", exc)
        await asyncio.sleep(L1_INTERVAL)


# ────────────────────────── 生命周期 ──────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理器。"""
    states = [
        "storage", "current_account", "price_fetcher", "cleanup_task",
        "empire_http", "empire_ws", "empire_last_event",
        "market_engine", "auction_engine", "balance_monitor",
        "trade_executor", "multi_account",
    ]
    for s in states:
        setattr(app.state, s, None)

    await init_db()
    logger.info("数据库已初始化: %s", LOG_DIR)

    # 前端 WebSocket 服务器 (8081)
    ws_server = None
    try:
        import websockets
        ws_server = await websockets.serve(frontend_ws_handler, "127.0.0.1", 8081)
        logger.info("前端 WS 服务已启动: ws://127.0.0.1:8081")
    except Exception as exc:
        logger.warning("前端 WS 启动失败 (端口 8081 可能被占用): %s", exc)

    # 价格服务
    fetcher = _init_price_fetcher(app)
    if fetcher:
        app.state.cleanup_task = _start_cleanup_task(fetcher)

    logger.info("服务启动完成 — http://127.0.0.1:8080 | Swagger: http://127.0.0.1:8080/docs")
    yield

    # ── 关闭清理 ──
    for engine_key in ("multi_account", "balance_monitor", "auction_engine", "market_engine"):
        engine = getattr(app.state, engine_key, None)
        if engine:
            try:
                if hasattr(engine, "stop_all"): await engine.stop_all()
                elif hasattr(engine, "stop"): await engine.stop()
                elif hasattr(engine, "stop_monitoring"): await engine.stop_monitoring()
            except Exception:
                pass

    if app.state.empire_ws:
        try: await app.state.empire_ws.disconnect()
        except Exception: pass
    if app.state.empire_http:
        try: await app.state.empire_http.close()
        except Exception: pass
    if app.state.cleanup_task:
        app.state.cleanup_task.cancel()
        try: await app.state.cleanup_task
        except asyncio.CancelledError: pass
    if ws_server:
        ws_server.close()
        await ws_server.wait_closed()

    logger.info("服务已关闭")


app.router.lifespan_context = lifespan

# ────────────────────────── 路由注册 ──────────────────────────

app.include_router(api_router)
app.include_router(accounts_router, prefix="/api/accounts", tags=["accounts"])
app.include_router(config_router, prefix="/api/config", tags=["config"])
app.include_router(prices_router, prefix="/api/prices", tags=["prices"])
app.include_router(empire_router, prefix="/api/empire", tags=["empire"])
app.include_router(auction_router, prefix="/api/auction", tags=["auction"])
app.include_router(market_router, prefix="/api/market", tags=["market"])
app.include_router(stats_router, prefix="/api/stats", tags=["stats"])
app.include_router(strategy_router, prefix="/api/strategy", tags=["strategy"])


# ────────────────────────── 健康检查 ──────────────────────────

@app.get("/api/health", response_model=HealthResponse)
@app.get("/api/health/", include_in_schema=False, response_model=HealthResponse)
async def health_check() -> HealthResponse:
    data_dir = Path.home() / ".csgoempire-bot"
    return HealthResponse(status="ok", path_exists=data_dir.exists())


# ────────────────────────── 余额查询 ──────────────────────────

@app.get("/api/balance")
@app.get("/api/balance/", include_in_schema=False)
async def get_balance(request: Request) -> dict:
    monitor = request.app.state.balance_monitor
    if monitor and monitor.last_balance:
        return {"status": "ok", "cached": True, "balance": monitor.last_balance}

    http_client = request.app.state.empire_http
    if http_client is None:
        return {"status": "error", "detail": "未连接 Empire"}

    try:
        balance = await asyncio.wait_for(http_client.get_balance(), timeout=10.0)
        return {"status": "ok", "cached": False, "balance": balance}
    except asyncio.TimeoutError:
        return {"status": "error", "detail": "余额查询超时"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


# ────────────────────────── 数据导入导出 ──────────────────────────

@app.post("/api/export")
async def export_data() -> dict:
    data_dir = Path.home() / ".csgoempire-bot"
    export: dict = {"version": "1.0.0"}
    config_file = data_dir / "config.json"
    if config_file.exists():
        export["config"] = json.loads(config_file.read_text(encoding="utf-8"))
    import base64
    for db_name in ["data.db", "accounts.db"]:
        db_path = data_dir / db_name
        if db_path.exists():
            export[f"_{db_name}_base64"] = base64.b64encode(db_path.read_bytes()).decode()
    return {"status": "ok", "data": export}


@app.post("/api/import")
async def import_data(request: Request) -> dict:
    try:
        body = await request.json()
        payload = body if isinstance(body, dict) else body.get("data", {})
    except Exception:
        return {"status": "error", "detail": "无效的 JSON 数据"}
    if not isinstance(payload, dict):
        return {"status": "error", "detail": "数据格式错误"}
    data_dir = Path.home() / ".csgoempire-bot"
    data_dir.mkdir(parents=True, exist_ok=True)
    if "config" in payload and isinstance(payload["config"], dict):
        (data_dir / "config.json").write_text(
            json.dumps(payload["config"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    import base64
    for db_name in ["data.db", "accounts.db"]:
        key = f"_{db_name}_base64"
        if key in payload:
            (data_dir / db_name).write_bytes(base64.b64decode(payload[key]))
    return {"status": "ok", "detail": "导入成功，请重启服务以加载新数据"}


# ────────────────────────── 直接运行入口 ──────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "127.0.0.1")
    logger.info("启动服务: %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_config=None)
