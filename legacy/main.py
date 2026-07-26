# main.py
# FastAPI 后端基础框架：账号管理、数据库、加密存储

from typing import List

from fastapi import FastAPI, HTTPException, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal, init_db
from models import Account
from security import encrypt_data, decrypt_data

app = FastAPI(
    title="CSGOEmpire Auction Helper Phase 1",
    description="Phase 1 后端基础框架，提供账号管理 API 和加密存储。",
    version="1.0.0",
)


async def get_db() -> AsyncSession:
    """获取数据库会话依赖。"""
    async with AsyncSessionLocal() as session:
        yield session


@app.on_event("startup")
async def on_startup() -> None:
    """应用启动时初始化数据库。"""
    await init_db()


@app.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_account(
    username: str,
    password: str,
    secret: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建新账号，并加密存储敏感数据。"""
    query = select(Account).where(Account.username == username)
    existing = await db.execute(query)
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="账号已存在")

    encrypted_data = encrypt_data(secret, password)
    account = Account(username=username, encrypted_data=encrypted_data, is_active=True)
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return {"id": account.id, "username": account.username, "is_active": account.is_active}


@app.get("/accounts", response_model=List[dict])
async def list_accounts(db: AsyncSession = Depends(get_db)) -> List[dict]:
    """列出所有账号信息（不包括敏感字段）。"""
    query = select(Account)
    result = await db.execute(query)
    accounts = result.scalars().all()
    return [
        {
            "id": account.id,
            "username": account.username,
            "is_active": account.is_active,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
        }
        for account in accounts
    ]


@app.get("/accounts/{account_id}")
async def get_account(account_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """获取单个账号详情（不包含加密信息）。"""
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return {
        "id": account.id,
        "username": account.username,
        "is_active": account.is_active,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }


@app.put("/accounts/{account_id}")
async def update_account(
    account_id: int,
    username: str = None,
    password: str = None,
    secret: str = None,
    is_active: bool = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """更新账号信息或重新加密敏感数据。"""
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")

    if username is not None:
        account.username = username
    if is_active is not None:
        account.is_active = is_active
    if secret is not None and password is not None:
        account.encrypted_data = encrypt_data(secret, password)

    db.add(account)
    await db.commit()
    await db.refresh(account)
    return {
        "id": account.id,
        "username": account.username,
        "is_active": account.is_active,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }


@app.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)) -> None:
    """删除指定账号。"""
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    await db.delete(account)
    await db.commit()
    return None


@app.post("/accounts/{account_id}/switch")
async def switch_account(account_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """切换当前账号为指定账号。此接口示例返回目标账号状态。"""
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return {"id": account.id, "username": account.username, "is_active": account.is_active}


@app.post("/accounts/{account_id}/decrypt")
async def decrypt_account_data(account_id: int, password: str, db: AsyncSession = Depends(get_db)) -> dict:
    """解密指定账号存储的数据。仅用于测试或本地使用。"""
    account = await db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    try:
        decrypted = decrypt_data(account.encrypted_data, password)
    except Exception:
        raise HTTPException(status_code=400, detail="解密失败，密码不正确")
    return {"id": account.id, "username": account.username, "decrypted_data": decrypted}
