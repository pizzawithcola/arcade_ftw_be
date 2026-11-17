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


async def test_the_boards_rank_the_event_the_console_chose(client: AsyncClient):
    """The wall and the waiting room read this endpoint, and neither of them is
    a place to pick an event: the choice has to come from the console."""
    ftw = await client.post(
        "/api/auth/player", json={"nickname": "Board-Ftw", "event": "hk_fintech_week"}
    )
    emp = await client.post(
        "/api/auth/player", json={"nickname": "Board-Emp", "event": "hk_employee"}
    )
    for body, points in ((ftw.json(), 300), (emp.json(), 900)):
        await client.put(
            f"/api/players/{body['player_id']}/score",
            json={"hallucination_score": points},
            headers=FULL,
        )
    try:
        # Nothing chosen: every event at once, which is how this used to read.
        together = (await client.get("/api/leaderboard?limit=50")).json()
        assert together["event"] is None
        assert {"Board-Ftw", "Board-Emp"} <= {row["nickname"] for row in together["players"]}

        await client.put("/api/settings", json={"active_event": "hk_employee"}, headers=FULL)
        scoped = (await client.get("/api/leaderboard?limit=50")).json()
        assert scoped["event"] == "hk_employee"
        names = {row["nickname"] for row in scoped["players"]}
        assert "Board-Emp" in names
        assert "Board-Ftw" not in names, "another event's player is on the board"
        # Ordered by score inside the event. Other files leave players behind,
        # so this checks the ordering rather than a specific first place.
        scores = [row["total_score"] for row in scoped["players"]]
        assert scores == sorted(scores, reverse=True)
        mine = next(row for row in scoped["players"] if row["nickname"] == "Board-Emp")
        assert mine["total_score"] == 900

        # A one-off look at another event does not disturb the stored choice.
        other = (await client.get("/api/leaderboard?limit=50&event=hk_fintech_week")).json()
        assert other["event"] == "hk_fintech_week"
        assert "Board-Ftw" in {row["nickname"] for row in other["players"]}
        still = (await client.get("/api/settings", headers=DISPLAY)).json()
        assert still["active_event"] == "hk_employee"

        # `all` restores the mixed board without clearing the setting.
        mixed = (await client.get("/api/leaderboard?limit=50&event=all")).json()
        assert mixed["event"] is None

        # Clearing it goes back to every event.
        await client.put("/api/settings", json={"active_event": None}, headers=FULL)
        cleared = (await client.get("/api/leaderboard?limit=50")).json()
        assert cleared["event"] is None
    finally:
        for body in (ftw.json(), emp.json()):
            await client.delete(f"/api/players/{body['player_id']}", headers=FULL)


async def test_an_unknown_active_event_is_refused(client: AsyncClient):
    response = await client.put("/api/settings", json={"active_event": "mars"}, headers=FULL)
    assert response.status_code == 422
    assert "mars" in response.json()["detail"]


async def test_saving_one_setting_leaves_the_other_alone(client: AsyncClient):
    """The module picker and the event switch are on two different pages, so
    neither may wipe the other."""
    await client.put("/api/settings", json={"modules": SHORT}, headers=FULL)
    await client.put("/api/settings", json={"active_event": "hk_employee"}, headers=FULL)
    assert (await client.get("/api/settings", headers=DISPLAY)).json()["modules"] == SHORT

    await client.put("/api/settings", json={"modules": list(MODULES)}, headers=FULL)
    assert (await client.get("/api/settings", headers=DISPLAY)).json()["active_event"] == "hk_employee"


async def test_the_room_wears_dark_until_the_console_says_otherwise(client: AsyncClient):
    """Dark is the show's own look, so a fresh install reads it without a row."""
    assert (await client.get("/api/settings", headers=DISPLAY)).json()["theme"] == "dark"

    saved = (await client.put("/api/settings", json={"theme": "light"}, headers=FULL)).json()
    assert saved["theme"] == "light"
    assert (await client.get("/api/settings", headers=DISPLAY)).json()["theme"] == "light"
    # And it survives a restart: the value is a row, not a process variable.
    async with SessionLocal() as session:
        stored = await session.scalar(text("select value from app_settings where key = 'theme'"))
    assert stored == "light"


async def test_an_unknown_theme_is_refused_and_nothing_is_stored(client: AsyncClient):
    """A typo would leave every screen unstyled: no variables, no colours."""
    response = await client.put("/api/settings", json={"theme": "sepia"}, headers=FULL)
    assert response.status_code == 422
    assert "sepia" in response.json()["detail"]
    assert (await client.get("/api/settings", headers=DISPLAY)).json()["theme"] == "dark"


async def test_the_theme_does_not_disturb_the_other_settings(client: AsyncClient):
    await client.put("/api/settings", json={"modules": SHORT}, headers=FULL)
    await client.put("/api/settings", json={"theme": "light"}, headers=FULL)
    body = (await client.get("/api/settings", headers=DISPLAY)).json()
    assert body["modules"] == SHORT
    assert body["theme"] == "light"


async def test_any_screen_can_read_the_theme_without_a_token(client: AsyncClient):
    """The login page has no session and the public board has no room socket, so
    the theme has to be readable without either. It is a colour, not a secret."""
    assert (await client.get("/api/theme")).json()["theme"] == "dark"
    await client.put("/api/settings", json={"theme": "light"}, headers=FULL)
    assert (await client.get("/api/theme")).json()["theme"] == "light"
