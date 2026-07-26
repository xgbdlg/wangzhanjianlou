# models.py
# 定义 SQLAlchemy ORM 模型：账户管理、加密存储

from sqlalchemy import Column, Integer, String, Boolean, DateTime, func
from database import Base

class Account(Base):
    """存储 CSGOEmpire 账号信息的 ORM 模型。"""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(128), unique=True, nullable=False, index=True)
    encrypted_data = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<Account id={self.id} username={self.username} active={self.is_active}>"
