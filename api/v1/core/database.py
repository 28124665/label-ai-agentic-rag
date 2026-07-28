"""
数据库连接管理模块
负责：
1. 创建数据库引擎和会话
2. 提供异步数据库会话依赖
3. 管理数据库连接生命周期
"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from typing import AsyncGenerator
import logging

from core.config import settings

logger = logging.getLogger(__name__)

# 创建异步引擎
engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG, pool_pre_ping=True, pool_size=10, max_overflow=20)

# 创建异步会话工厂
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""

    pass


async def init_db():
    """初始化数据库（创建表）。

    导入所有 ORM 模型以确保它们被注册到 ``Base.metadata``，
    否则 ``create_all`` 不会创建任何表。
    """
    try:
        # 导入所有模型以确保它们被注册到 Base.metadata
        # noqa: F401 表示这些导入仅用于副作用（注册模型）
        from models import (  # noqa: F401
            agent,
            audit_log,
            conversation,
            datasource,
            message,
            model_config,
            user,
        )

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise


async def close_db():
    """关闭数据库连接"""
    await engine.dispose()
    logger.info("Database engine disposed")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话依赖（用于 FastAPI 的 Depends）。

    注意：本依赖不再自动 commit。Service 层应在事务边界显式调用
    ``await session.commit()``，避免双重 commit 破坏事务原子性。
    本依赖仅负责在异常时回滚并最终关闭会话。

    使用示例：
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with async_session() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
