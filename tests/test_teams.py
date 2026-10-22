"""Team assignment: splitting a room into Red and Blue for Sabotage."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import ADMIN_SUBJECT, SCOPE_FULL, create_token
from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer " + create_token(ADMIN_SUBJECT, SCOPE_FULL)}


async def _login(client: AsyncClient, nickname: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/player", json={"nickname": nickname, "event": "hk_employee"}
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}


async def _join(client: AsyncClient, headers: dict[str, str], code: str) -> None:
    joined = await client.post(f"/api/rooms/{code}/join", headers=headers)
    assert joined.status_code == 200


async def _room(client: AsyncClient, admin_headers: dict[str, str], code: str) -> None:
    created = await client.post("/api/rooms", json={"code": code}, headers=admin_headers)
    assert created.status_code == 201


async def test_assign_splits_as_evenly_as_possible(
    client: AsyncClient, admin_headers: dict
) -> None:
    await _room(client, admin_headers, "teams-even")
    for name in ("One", "Two", "Three", "Four"):
        headers = await _login(client, name)
        await _join(client, headers, "teams-even")

    assigned = await client.post(
        "/api/rooms/teams-even/teams/assign", headers=admin_headers
    )
    assert assigned.status_code == 200
    body = assigned.json()
    assert len(body["red"]) == 2
    assert len(body["blue"]) == 2


async def test_assign_is_deterministic_and_reenassignable(
    client: AsyncClient, admin_headers: dict
) -> None:
    """A replayed round lands the same players on the same side."""
    await _room(client, admin_headers, "teams-reassign")
    names = ("One", "Two", "Three")
    player_headers = []
    for name in names:
        headers = await _login(client, name)
        await _join(client, headers, "teams-reassign")
        player_headers.append(headers)

    first = await client.post(
        "/api/rooms/teams-reassign/teams/assign", headers=admin_headers
    )
    second = await client.post(
        "/api/rooms/teams-reassign/teams/assign", headers=admin_headers
    )
    assert first.json()["red"] == second.json()["red"]
    assert first.json()["blue"] == second.json()["blue"]


async def test_odd_room_gives_red_the_extra_player(
    client: AsyncClient, admin_headers: dict
) -> None:
    await _room(client, admin_headers, "teams-odd")
    for name in ("One", "Two", "Three"):
        headers = await _login(client, name)
        await _join(client, headers, "teams-odd")

    assigned = await client.post(
        "/api/rooms/teams-odd/teams/assign", headers=admin_headers
    )
    body = assigned.json()
    assert len(body["red"]) == 2
    assert len(body["blue"]) == 1


async def test_roster_hides_teams_from_players_but_not_admins(
    client: AsyncClient, admin_headers: dict
) -> None:
    await _room(client, admin_headers, "teams-roster")
    player_headers = await _login(client, "RosterPlayer")
    await _join(client, player_headers, "teams-roster")
    await client.post("/api/rooms/teams-roster/teams/assign", headers=admin_headers)

    admin_view = await client.get(
        "/api/rooms/teams-roster/players", headers=admin_headers
    )
    assert admin_view.json()["players"][0]["team"] in ("red", "blue")

    player_view = await client.get(
        "/api/rooms/teams-roster/players", headers=player_headers
    )
    assert "team" not in player_view.json()["players"][0]


async def test_me_reports_the_players_own_team(
    client: AsyncClient, admin_headers: dict
) -> None:
    await _room(client, admin_headers, "teams-me")
    player_headers = await _login(client, "TeamMe")
    await _join(client, player_headers, "teams-me")

    before = await client.get("/api/auth/me", headers=player_headers)
    assert before.json()["team"] is None

    await client.post("/api/rooms/teams-me/teams/assign", headers=admin_headers)
    after = await client.get("/api/auth/me", headers=player_headers)
    assert after.json()["team"] in ("red", "blue")


async def test_joining_a_new_room_clears_the_team(
    client: AsyncClient, admin_headers: dict
) -> None:
    await _room(client, admin_headers, "teams-a")
    await _room(client, admin_headers, "teams-b")
    player_headers = await _login(client, "Mover")
    await _join(client, player_headers, "teams-a")
    await client.post("/api/rooms/teams-a/teams/assign", headers=admin_headers)

    in_a = await client.get("/api/auth/me", headers=player_headers)
    assert in_a.json()["team"] in ("red", "blue")

    await _join(client, player_headers, "teams-b")
    in_b = await client.get("/api/auth/me", headers=player_headers)
    assert in_b.json()["team"] is None


async def test_assign_requires_admin(client: AsyncClient, admin_headers: dict) -> None:
    await _room(client, admin_headers, "teams-auth")
    player_headers = await _login(client, "NotAdmin")
    response = await client.post(
        "/api/rooms/teams-auth/teams/assign", headers=player_headers
    )
    assert response.status_code == 401
