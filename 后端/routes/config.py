# routes/config.py
# 运行时配置路由：允许前端 UI 设置 cs2.sh API Key

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger("routes.config")

CONFIG_FILE = Path.home() / ".csgoempire-bot" / "config.json"
router = APIRouter()


class ConfigStatus(BaseModel):
    """配置状态响应。"""
    cs2sh_key_configured: bool
    cs2sh_key_masked: Optional[str] = None  # 脱敏显示，如 "cs2_****a1b2"


class SetApiKeyRequest(BaseModel):
    """设置 API Key 请求体。"""
    api_key: str


def _load_config() -> dict:
    """从磁盘加载配置。"""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_config(config: dict) -> None:
    """保存配置到磁盘。"""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _mask_key(api_key: str) -> str:
    """脱敏显示 API Key，只显示前 4 位和后 4 位。"""
    if len(api_key) <= 8:
        return api_key[:2] + "****"
    return api_key[:4] + "****" + api_key[-4:]


# ────────────────────────── GET /api/config ──────────────────────────

@router.get("", response_model=ConfigStatus)
@router.get("/", include_in_schema=False, response_model=ConfigStatus)
async def get_config(request: Request) -> ConfigStatus:
    """获取当前配置状态（cs2.sh API Key 是否已设置）。"""
    fetcher = request.app.state.price_fetcher
    configured = fetcher is not None
    masked = _mask_key(fetcher.api_key) if configured else None
    return ConfigStatus(cs2sh_key_configured=configured, cs2sh_key_masked=masked)


# ────────────────────────── POST /api/config ──────────────────────────

@router.post("", response_model=ConfigStatus)
@router.post("/", include_in_schema=False, response_model=ConfigStatus)
async def set_config(body: SetApiKeyRequest, request: Request) -> ConfigStatus:
    """设置或更新 cs2.sh API Key（前端 UI 填入 Key 后调用此接口）。

    持久化到 ~/.csgoempire-bot/config.json，重启后自动加载。
    """
    from database import AsyncSessionLocal
    from services.price_fetcher import CS2PriceFetcher

    api_key = body.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key 不能为空")

    # 持久化到磁盘
    config = _load_config()
    config["cs2sh_api_key"] = api_key
    _save_config(config)

    # 重新初始化价格服务
    fetcher = CS2PriceFetcher(api_key=api_key, session_factory=AsyncSessionLocal)
    request.app.state.price_fetcher = fetcher

    # 如果后台清理任务未启动，则启动
    if request.app.state.cleanup_task is None:
        import asyncio
        from main import _cache_cleanup_loop

        request.app.state.cleanup_task = asyncio.create_task(
            _cache_cleanup_loop(fetcher)
        )

    logger.info("cs2.sh API Key 已通过 UI 设置并持久化")
    return ConfigStatus(cs2sh_key_configured=True, cs2sh_key_masked=_mask_key(api_key))


# ────────────────────────── 加密导出 ──────────────────────────

class ExportRequest(BaseModel):
    master_password: str


class ImportRequest(BaseModel):
    encrypted_data: str
    master_password: str


@router.post("/export", response_model=dict)
@router.post("/export/", include_in_schema=False, response_model=dict)
async def config_export(body: ExportRequest, request: Request) -> dict:
    """加密导出所有配置和账号数据。

    使用主密码（Fernet）加密整个 JSON 包，返回 base64 密文。
    """
    from security import SecureStorage

    storage = request.app.state.storage
    if storage is None:
        # 用提供的主密码临时创建
        storage = SecureStorage(master_password=body.master_password)
        storage.init_storage()

    # 打包数据
    payload = {
        "version": "1.0.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "accounts": storage.list_accounts(),  # 不含明文 API key
        "config": _load_config(),
    }

    # 获取所有账号的完整数据（含解密后的 API key）
    accounts_full = []
    for acc in payload["accounts"]:
        full = storage.get_account(acc["name"])
        if full:
            accounts_full.append(full)
    payload["accounts"] = accounts_full

    # Fernet 加密（处理 datetime 序列化）
    def _json_default(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    payload_json = json.dumps(payload, ensure_ascii=False, default=_json_default)
    encrypted = storage.encrypt(payload_json)

    import base64
    return {
        "status": "ok",
        "encrypted_data": base64.b64encode(encrypted).decode("ascii"),
        "account_count": len(accounts_full),
    }


@router.post("/import", response_model=dict)
@router.post("/import/", include_in_schema=False, response_model=dict)
async def config_import(body: ImportRequest, request: Request) -> dict:
    """解密导入配置和账号数据。

    用主密码解密 base64 密文，验证版本后导入。
    """
    import base64
    from security import SecureStorage

    # 用提供的主密码创建临时 storage 来解密
    try:
        temp_storage = SecureStorage(master_password=body.master_password)
        temp_storage.init_storage()
        encrypted_bytes = base64.b64decode(body.encrypted_data)
        decrypted = temp_storage.decrypt(encrypted_bytes)
        payload = json.loads(decrypted)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"解密失败（主密码错误或数据损坏）: {exc}")

    # 使用服务器 storage 进行后续写入
    storage = request.app.state.storage
    if storage is None:
        storage = SecureStorage(master_password=body.master_password)
        storage.init_storage()
        request.app.state.storage = storage

    # 版本检查
    version = payload.get("version", "0.0.0")
    if version.split(".")[0] != "1":
        raise HTTPException(status_code=400, detail=f"版本不兼容: {version}（需要 1.x）")

    imported_accounts = 0

    # 导入账号
    for acc in payload.get("accounts", []):
        name = acc.get("name", "")
        api_key = acc.get("api_key", "")
        empire_rate = acc.get("empire_rate", 0.65)
        if name and api_key:
            try:
                storage.save_account(name, api_key, empire_rate)
                imported_accounts += 1
            except Exception as exc:
                logger.warning("导入账号 [%s] 失败: %s", name, exc)

    # 导入配置
    if "config" in payload and isinstance(payload["config"], dict):
        config = _load_config()
        for k, v in payload["config"].items():
            config[k] = v
        _save_config(config)

    logger.info("导入完成: %d 个账号", imported_accounts)
    return {"status": "ok", "imported_accounts": imported_accounts}


