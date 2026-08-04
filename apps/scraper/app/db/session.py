from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# 经 SSH/autossh 隧道连库：隧道抖动会让池内连接客户端侧失效，
# 服务端 orphan 连接收不到 FIN 又无 keepalive，会堆成 idle 直到打满 max_connections。
# pre_ping 检出死连接并丢弃，recycle 定期主动重建，pool 上限收紧 → 连接数封顶可控。
engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_recycle=180,
    pool_size=3,
    max_overflow=2,
    pool_timeout=30,
)

async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
