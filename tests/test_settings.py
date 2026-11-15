"""The console setting that has to outlive the browser.

The module set for the next room used to be kept in `localStorage`, so the venue
laptop and the operator's own could disagree, and a cleared cache silently
restored the full show. It is stored on the server now, which is what these
tests pin: the value survives a request, and a bad value cannot be stored.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.auth import ADMIN_SUBJECT, SCOPE_FULL, create_token
from app.database import SessionLocal
from app.flow import MODULES
from app.main import app

FULL = {"Authorization": "Bearer " + create_token(ADMIN_SUBJECT, SCOPE_FULL)}
DISPLAY = {"Authorization": "Bearer " + create_token(ADMIN_SUBJECT, "display")}

SHORT = ["welcome", "hallucination", "data", "ultimate", "total", "wrapup"]


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    # The setting is global, so a test that wrote one would hand it to every
    # room built afterwards - including other files' rooms.
    async with SessionLocal() as session:
        await session.execute(text("delete from app_settings"))
        await session.commit()


async def test_the_default_is_the_whole_show(client: AsyncClient):
    body = (await client.get("/api/settings", headers=DISPLAY)).json()
    assert body["modules"] == list(MODULES)


async def test_a_saved_set_comes_back_in_the_shows_order(client: AsyncClient):
    # Sent out of order on purpose: the request order is the operator's clicking,
    # the stored order is the rundown's.
    saved = await client.put(
        "/api/settings", json={"modules": list(reversed(SHORT))}, headers=FULL
    )
    assert saved.status_code == 200
    assert saved.json()["modules"] == SHORT

    read = (await client.get("/api/settings", headers=DISPLAY)).json()
    assert read["modules"] == SHORT
    # And it is the database, not a request-scoped value.
    async with SessionLocal() as session:
        raw = await session.scalar(text("select value from app_settings where key = 'next_modules'"))
    assert "hallucination" in raw


async def test_saving_twice_replaces_rather_than_appends(client: AsyncClient):
    await client.put("/api/settings", json={"modules": SHORT}, headers=FULL)
    again = await client.put("/api/settings", json={"modules": ["welcome", "total"]}, headers=FULL)
    assert again.json()["modules"] == ["welcome", "total"]
    assert (await client.get("/api/settings", headers=DISPLAY)).json()["modules"] == [
        "welcome",
        "total",
    ]


async def test_a_bad_set_is_refused_and_nothing_is_stored(client: AsyncClient):
    for payload, detail in (
        ({"modules": ["welcome", "battle"]}, "unknown"),
        ({"modules": []}, "at least one"),
    ):
        response = await client.put("/api/settings", json=payload, headers=FULL)
        assert response.status_code == 422, payload
        assert detail in response.json()["detail"]
    # The refusal left the default in place rather than a half-written value.
    assert (await client.get("/api/settings", headers=DISPLAY)).json()["modules"] == list(MODULES)


async def test_the_setting_is_readable_but_not_writable_without_full_scope(
    client: AsyncClient,
):
    assert (await client.get("/api/settings", headers=DISPLAY)).status_code == 200
    assert (
        await client.put("/api/settings", json={"modules": SHORT}, headers=DISPLAY)
    ).status_code == 401
    assert (await client.get("/api/settings")).status_code == 401
