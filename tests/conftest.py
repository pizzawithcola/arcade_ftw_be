import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings


async def _reset_schema() -> None:
    engine = create_async_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    """Build the schema from the real migrations, never from create_all.

    Runs synchronously on purpose: the Alembic env uses asyncio.run internally,
    which cannot be nested inside a running event loop.
    """
    asyncio.run(_reset_schema())
    command.upgrade(Config("alembic.ini"), "head")
