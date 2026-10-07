"""MySQL 连接池模块（SQLAlchemy async）"""
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.config import settings

# 注意：aiomysql 0.2.0 与 SQLAlchemy 2.0.36 搭配时，pool_pre_ping=True 会在
# 检出新连接时触发 ping() 参数签名不兼容（AsyncAdapt_aiomysql_connection.ping
# 缺少 reconnect 参数）导致异常。故禁用 pre_ping，改用短 pool_recycle 兜底
# 刷新过期连接，兼顾稳定性。
engine = create_async_engine(
    settings.mysql_url,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
    pool_pre_ping=False,
    echo=False,
)

# 异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncSession:
    """获取数据库会话（FastAPI 依赖注入）"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
