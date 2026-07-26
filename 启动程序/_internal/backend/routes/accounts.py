# routes/accounts.py
# 账号管理路由：账号增删改查、切换、当前账号查询

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Account
from schemas import (
    AccountCreateRequest,
    AccountCreateResponse,
    AccountListResponse,
    AccountResponse,
    AccountSwitchResponse,
    CurrentAccountResponse,
    StatusResponse,
)
from security import SecureStorage

router = APIRouter()


def get_storage(request: Request) -> SecureStorage:
    """获取 SecureStorage 实例，优先使用请求头中的主密码。"""
    header_password = request.headers.get("X-Master-Password")
    storage = request.app.state.storage
    if header_password:
        storage = SecureStorage(master_password=header_password)
        storage.init_storage()
        request.app.state.storage = storage
    if storage is None:
        raise HTTPException(status_code=400, detail="Secure storage 未初始化，请先调用 /api/init 或传入 X-Master-Password")
    return storage


@router.post("", response_model=AccountCreateResponse)
@router.post("/", include_in_schema=False, response_model=AccountCreateResponse)
async def create_account(
    body: AccountCreateRequest,
    db: AsyncSession = Depends(get_db),
    storage: SecureStorage = Depends(get_storage),
) -> AccountCreateResponse:
    """创建账号并同步到独立加密存储和主数据库。"""
    query = select(Account).where(Account.name == body.name)
    result = await db.execute(query)
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="账号已存在")

    encrypted_api_key = storage.encrypt(body.api_key)
    account = Account(name=body.name, api_key_encrypted=encrypted_api_key, empire_rate=body.empire_rate)

    try:
        storage.save_account(body.name, body.api_key, body.empire_rate)
        db.add(account)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        try:
            storage.delete_account(body.name)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"账号保存失败：{exc}")

    return {
        "status": "ok",
        "account": {
            "name": body.name,
            "empire_rate": body.empire_rate,
        },
    }


@router.get("", response_model=AccountListResponse)
@router.get("/", include_in_schema=False, response_model=AccountListResponse)
async def list_accounts(db: AsyncSession = Depends(get_db)) -> AccountListResponse:
    """列出所有账号信息，不包含 api_key。"""
    query = select(Account)
    result = await db.execute(query)
    accounts = result.scalars().all()
    return {
        "accounts": [
            {
                "name": account.name,
                "empire_rate": account.empire_rate,
                "created_at": account.created_at.isoformat() if account.created_at else None,
            }
            for account in accounts
        ]
    }


@router.post("/{name}/switch", response_model=AccountSwitchResponse)
@router.post("/{name}/switch/", include_in_schema=False, response_model=AccountSwitchResponse)
async def switch_account(name: str, request: Request, db: AsyncSession = Depends(get_db)) -> AccountSwitchResponse:
    """切换当前激活账号至指定账号。"""
    query = select(Account).where(Account.name == name)
    result = await db.execute(query)
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    request.app.state.current_account = name
    return {"status": "ok", "current_account": name}


@router.delete("/{name}", response_model=StatusResponse)
@router.delete("/{name}/", include_in_schema=False, response_model=StatusResponse)
async def delete_account(
    name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    storage: SecureStorage = Depends(get_storage),
) -> StatusResponse:
    """删除账号及其加密存储。"""
    query = select(Account).where(Account.name == name)
    result = await db.execute(query)
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    try:
        storage.delete_account(name)
        await db.delete(account) 
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"账号删除失败：{exc}")

    if request.app.state.current_account == name:
        request.app.state.current_account = None

    return {"status": "ok"}


@router.get("/current", response_model=CurrentAccountResponse)
@router.get("/current/", include_in_schema=False, response_model=CurrentAccountResponse)
async def get_current_account(request: Request) -> CurrentAccountResponse:
    """获取当前激活账号名。"""
    return {"current_account": request.app.state.current_account}
