from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:
            # Info, not error: an HTTPException raised by a handler unwinds
            # through here too, and those are already reported as 4xx by the
            # access middleware. Genuine failures are logged by whoever raised
            # them; this line only records that the transaction was discarded,
            # and the error_type is what distinguishes a routine 404 unwind
            # from a real database fault.
            log.info(
                "database session rolled back",
                extra={"error_type": type(exc).__name__},
            )
            await session.rollback()
            raise
