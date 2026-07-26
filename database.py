# database.py
# SQLite 数据库连接和 SQLAlchemy ORM 初始化

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite+aiosqlite:///./csgoempire_auction.db"

# 声明性基础类
Base = declarative_base()

# 异步数据库引擎
engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
)

# 异步会话工厂
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def init_db() -> None:
    """初始化数据库，如果表不存在则创建。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
