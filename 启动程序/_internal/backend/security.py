# security.py
# 提供 SecureStorage 类，用于在独立 SQLite 数据库中加密存储账号信息

import base64
import hashlib
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import Account

STORAGE_SALT = b"csgoempire_bot_salt_v1"
ACCOUNTS_DIR = Path.home() / ".csgoempire-bot"
ACCOUNTS_DB_PATH = ACCOUNTS_DIR / "accounts.db"
ACCOUNTS_DB_URL = f"sqlite:///{ACCOUNTS_DB_PATH}"


class SecureStorage:
    """加密存储管理器，保存账号敏感信息到独立账号数据库。"""

    def __init__(self, master_password: str) -> None:
        self.master_password = master_password
        self.key = self._derive_key(master_password)
        self._engine = create_engine(ACCOUNTS_DB_URL, future=True, echo=False)
        ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)

    def _derive_key(self, password: str) -> bytes:
        """使用 PBKDF2 从主密码派生 Fernet 密钥。"""
        key_bytes = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            STORAGE_SALT,
            100000,
        )
        return base64.urlsafe_b64encode(key_bytes)

    def encrypt(self, text: str) -> bytes:
        """加密文本并返回字节串。"""
        f = Fernet(self.key)
        return f.encrypt(text.encode("utf-8"))

    def decrypt(self, encrypted: bytes) -> str:
        """解密字节串并返回明文文本。"""
        f = Fernet(self.key)
        return f.decrypt(encrypted).decode("utf-8")

    def init_storage(self) -> None:
        """初始化独立的账号存储数据库。"""
        from models import Account as AccountModel

        AccountModel.__table__.create(bind=self._engine, checkfirst=True)

    def save_account(self, name: str, api_key: str, empire_rate: float) -> None:
        """将账号信息加密后保存到 accounts.db。"""
        encrypted_api_key = self.encrypt(api_key)
        with Session(self._engine) as session:
            existing = session.execute(select(Account).where(Account.name == name)).scalar_one_or_none()
            if existing is not None:
                existing.api_key_encrypted = encrypted_api_key
                existing.empire_rate = empire_rate
                session.add(existing)
            else:
                account = Account(name=name, api_key_encrypted=encrypted_api_key, empire_rate=empire_rate)
                session.add(account)
            session.commit()

    def get_account(self, name: str) -> dict | None:
        """读取账号并返回解密后的 api_key。"""
        with Session(self._engine) as session:
            account = session.execute(select(Account).where(Account.name == name)).scalar_one_or_none()
            if account is None:
                return None
            return {
                "name": account.name,
                "empire_rate": account.empire_rate,
                "api_key": self.decrypt(account.api_key_encrypted),
                "created_at": account.created_at,
            }

    def list_accounts(self) -> list[dict]:
        """列出所有账号基本信息，不包含明文 api_key。"""
        with Session(self._engine) as session:
            accounts = session.execute(select(Account)).scalars().all()
            return [
                {
                    "name": account.name,
                    "empire_rate": account.empire_rate,
                    "created_at": account.created_at,
                }
                for account in accounts
            ]

    def delete_account(self, name: str) -> None:
        """删除指定账号（使用直接 DELETE 避免触发 relationship 懒加载）。"""
        from sqlalchemy import delete as sql_delete

        with Session(self._engine) as session:
            result = session.execute(sql_delete(Account).where(Account.name == name))
            session.commit()
            if result.rowcount == 0:
                return
