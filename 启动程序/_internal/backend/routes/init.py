# routes/init.py
# 初始化 API 路由：创建目录、初始化存储、插入默认策略配置

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Base, engine, get_db
from models import StrategyConfig
from schemas import InitRequest, StatusResponse
from security import SecureStorage

router = APIRouter()


@router.post("/init", response_model=StatusResponse)
@router.post("/init/", include_in_schema=False, response_model=StatusResponse)
async def initialize_system(
    request: Request,
    body: InitRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """初始化主数据库和安全存储，插入默认全局策略。"""
    data_dir = Path.home() / ".csgoempire-bot"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法创建数据目录：{exc}")

    # 初始化安全存储数据库
    storage = SecureStorage(master_password=body.master_password)
    storage.init_storage()
    request.app.state.storage = storage
    request.app.state.current_account = None

    # 初始化主数据库
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 插入默认全局策略（如果尚未存在）
    query = select(StrategyConfig).where(
        StrategyConfig.is_global == True,
        StrategyConfig.account_id.is_(None),
    )
    result = await db.execute(query)
    if result.scalars().first() is None:
        default_strategy = StrategyConfig(is_global=True)
        db.add(default_strategy)
        await db.commit()

    return StatusResponse(status="ok")
