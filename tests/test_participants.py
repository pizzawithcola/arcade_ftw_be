"""A room's history: who played it, and what they scored.

The console lists every room and expands one to see its roster, so this has to
answer for *closed* rooms too. That is the whole point: finishing a room clears
`players.room_code`, so the live roster endpoint reports nobody for every room
that has already been played.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import ADMIN_SUBJECT, SCOPE_FULL, create_token
from app.main import app

ADMIN = {"Authorization": "Bearer " + create_token(ADMIN_SUBJECT, SCOPE_FULL)}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _room(client: AsyncClient, code: str) -> None:
    response = await client.post("/api/rooms", json={"code": code}, headers=ADMIN)
    assert response.status_code == 201


async def _join(client: AsyncClient, code: str, nickname: str) -> int:
    login = await client.post("/api/auth/player", json={"nickname": nickname, "event": "hk_employee"})
    assert login.status_code == 200
    body = login.json()
    joined = await client.post(
        f"/api/rooms/{code}/join", headers={"Authorization": "Bearer " + body["token"]}
    )
    assert joined.status_code == 200
    return body["player_id"]


async def _cleanup(client: AsyncClient, code: str, nicknames: list[str]) -> None:
    await client.post(f"/api/rooms/{code}/finish", headers=ADMIN)
    await client.delete(f"/api/rooms/{code}", headers=ADMIN)
    listed = (await client.get("/api/players", headers=ADMIN)).json()["players"]
    for player in listed:
        if player["nickname"] in nicknames:
            await client.delete(f"/api/players/{player['player_id']}", headers=ADMIN)


async def test_a_closed_room_still_lists_who_played_it(client: AsyncClient):
    code = "PART-CLOSED"
    names = ["Part-One", "Part-Two"]
    await _room(client, code)
    first = await _join(client, code, names[0])
    await _join(client, code, names[1])
    try:
        scored = await client.put(
            f"/api/players/{first}/score",
            json={"hallucination_score": 100, "data_score": 250},
            headers=ADMIN,
        )
        assert scored.status_code == 200

        # Finishing is what used to wipe the roster from this endpoint's view.
        await client.post(f"/api/rooms/{code}/finish", headers=ADMIN)
        live = await client.get(f"/api/rooms/{code}/players", headers=ADMIN)
        assert live.json()["count"] == 0, "the live roster is expected to empty out"

        body = (await client.get(f"/api/rooms/{code}/participants", headers=ADMIN)).json()
        assert body["count"] == 2
        names_back = {row["nickname"] for row in body["players"]}
        assert names_back == set(names)
        # Best first, and the scores travel with the row.
        best = body["players"][0]
        assert best["nickname"] == names[0]
        assert best["total_score"] == 350
        assert best["score"]["data_score"] == 250
        # A player who never scored carries no score row rather than four zeroes.
        assert body["players"][1]["score"] is None
        assert body["players"][1]["joined_at"] is not None
    finally:
        await _cleanup(client, code, names)


async def test_an_unknown_room_is_refused(client: AsyncClient):
    response = await client.get("/api/rooms/NOPE-NOPE/participants", headers=ADMIN)
    assert response.status_code == 404


async def test_the_history_needs_a_token(client: AsyncClient):
    response = await client.get("/api/rooms/main/participants")
    assert response.status_code == 401
