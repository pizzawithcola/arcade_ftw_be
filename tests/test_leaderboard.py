"""The public leaderboard: top totals, no identity beyond a nickname."""

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


async def _player(client: AsyncClient, nickname: str) -> int:
    response = await client.post(
        "/api/auth/player", json={"nickname": nickname, "event": "hk_employee"}
    )
    assert response.status_code == 200
    return response.json()["player_id"]


async def _score(client: AsyncClient, headers: dict, player_id: int, total: int) -> None:
    response = await client.put(
        f"/api/players/{player_id}/score",
        json={"hallucination_score": total},
        headers=headers,
    )
    assert response.status_code == 200


async def test_leaderboard_is_public_and_orders_by_total(
    client: AsyncClient, admin_headers: dict
) -> None:
    # Unique prefix so a rerun never collides with the previous schema; high
    # totals so the trio always sits at the top regardless of other tests.
    prefix = "lb-order-"
    ids = [await _player(client, f"{prefix}{name}") for name in ("Low", "High", "Mid")]
    await _score(client, admin_headers, ids[0], 70000)
    await _score(client, admin_headers, ids[1], 90000)
    await _score(client, admin_headers, ids[2], 80000)

    # No token at all: the venue screen opens it cold.
    response = await client.get("/api/leaderboard")
    assert response.status_code == 200

    board = response.json()["players"]
    ranks = {row["nickname"]: row["rank"] for row in board}
    assert ranks[f"{prefix}High"] < ranks[f"{prefix}Mid"] < ranks[f"{prefix}Low"]
    # The public board carries no player id, event, team or room.
    assert all(set(row) == {"rank", "nickname", "total_score"} for row in board)


async def test_leaderboard_respects_the_limit(client: AsyncClient, admin_headers: dict) -> None:
    prefix = "lb-limit-"
    for name in ("A", "B", "C", "D"):
        pid = await _player(client, f"{prefix}{name}")
        await _score(client, admin_headers, pid, 100)

    response = await client.get("/api/leaderboard?limit=2")
    assert response.status_code == 200
    assert len(response.json()["players"]) == 2


async def test_players_without_a_score_row_read_as_zero(
    client: AsyncClient, admin_headers: dict
) -> None:
    prefix = "lb-zero-"
    scored = await _player(client, f"{prefix}Scored")
    await _score(client, admin_headers, scored, 99999)
    await _player(client, f"{prefix}Unscored")

    response = await client.get("/api/leaderboard?limit=50")
    board = response.json()["players"]
    by_name = {row["nickname"]: row for row in board}

    assert by_name[f"{prefix}Scored"]["total_score"] == 99999
    assert by_name[f"{prefix}Unscored"]["total_score"] == 0
    assert by_name[f"{prefix}Scored"]["rank"] < by_name[f"{prefix}Unscored"]["rank"]


async def test_room_leaderboard_ranks_one_section(
    client: AsyncClient, admin_headers: dict
) -> None:
    """The in-game board: a room's players ranked by one section's column."""
    prefix = "lb-room-"
    room = "lb-room"

    created = await client.post("/api/rooms", json={"code": room}, headers=admin_headers)
    assert created.status_code == 201

    ids: dict[str, int] = {}
    tokens: dict[str, dict] = {}
    for name in ("Low", "High", "Mid"):
        login = await client.post(
            "/api/auth/player", json={"nickname": f"{prefix}{name}", "event": "hk_employee"}
        )
        assert login.status_code == 200
        ids[name] = login.json()["player_id"]
        tokens[name] = {"Authorization": "Bearer " + login.json()["token"]}
        joined = await client.post(f"/api/rooms/{room}/join", headers=tokens[name])
        assert joined.status_code == 200

    # Set only the hallucination column; other sections stay at zero.
    for name, total in (("Low", 100), ("High", 900), ("Mid", 500)):
        await _score(client, admin_headers, ids[name], total)

    # A player token reads the board too - it is shown on the tablets.
    response = await client.get(
        f"/api/rooms/{room}/leaderboard?section=hallucination", headers=tokens["Low"]
    )
    assert response.status_code == 200
    board = response.json()["players"]
    assert [row["nickname"] for row in board] == [
        f"{prefix}High",
        f"{prefix}Mid",
        f"{prefix}Low",
    ]
    assert [row["score"] for row in board] == [900, 500, 100]
    assert [row["rank"] for row in board] == [1, 2, 3]


async def test_room_leaderboard_rejects_an_unknown_section(
    client: AsyncClient, admin_headers: dict
) -> None:
    await client.post("/api/rooms", json={"code": "lb-bad-section"}, headers=admin_headers)
    response = await client.get(
        "/api/rooms/lb-bad-section/leaderboard?section=nonsense", headers=admin_headers
    )
    assert response.status_code == 422
