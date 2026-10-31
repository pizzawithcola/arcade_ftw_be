import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings

# The room a show actually runs in. The migration seeds it and it must survive
# the suite: `/api/rooms/current` follows the newest *open* room, so leaving the
# suite's own rooms open would send every iPad to a test room next time.
DEFAULT_ROOM = "main"


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
    yield
    asyncio.run(_drop_test_rooms())


async def _drop_test_rooms() -> None:
    """Leave the database in the state a show needs.

    Every test room is deleted and the default room is reopened. Without this
    the suite leaves ~46 rooms open, `/api/rooms/current` picks the newest of
    them, and a dev server that was running the whole time answers a live iPad
    with a room whose engine it has never heard of.
    """
    engine = create_async_engine(settings.database_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        await connection.execute(text("delete from rooms where code <> :keep"), {"keep": DEFAULT_ROOM})
        await connection.execute(
            text("update rooms set closed_at = null where code = :keep"), {"keep": DEFAULT_ROOM}
        )
    await engine.dispose()
