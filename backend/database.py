# database.py
# SQLite 异步数据库连接和 SQLAlchemy ORM 初始化

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATA_DIR = Path.home() / ".csgoempire-bot"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_URL = f"sqlite+aiosqlite:///{DATA_DIR / 'data.db'}"

Base = declarative_base()

engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """初始化主数据库，自动创建必要表。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    """提供异步数据库会话依赖。"""
    async with AsyncSessionLocal() as session:
        yield session
