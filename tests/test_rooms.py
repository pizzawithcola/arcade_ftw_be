"""Rooms as data: the seeded room, joining one, and the roster it produces."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.auth import ADMIN_SUBJECT, SCOPE_DISPLAY, create_token
from app.database import SessionLocal
from app.main import app
from app.models import Player, Room, Score
from app.rooms import DEFAULT_ROOM, RoomRegistry, rooms


@pytest.fixture
async def client():
    # Make `main` exist up front: a test that joins the default room must not
    # depend on a previous test's teardown having created the engine.
    rooms.ensure(DEFAULT_ROOM)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    await rooms.ensure(DEFAULT_ROOM).finish()


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_token(ADMIN_SUBJECT)}"}


@pytest.fixture
def display_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_token(ADMIN_SUBJECT, SCOPE_DISPLAY)}"}


async def _player(client: AsyncClient, nickname: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/player", json={"nickname": nickname, "event": "hk_employee"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


async def test_the_migration_seeds_the_default_room() -> None:
    """The registry used to invent `main`; now the row has to be there."""
    async with SessionLocal() as session:
        assert await session.get(Room, DEFAULT_ROOM) is not None


async def test_startup_builds_an_engine_per_room() -> None:
    """Loading rooms from the database replaces the hardcoded default."""
    registry = RoomRegistry()
    await registry.load()
    assert DEFAULT_ROOM in registry.codes()


async def test_a_database_outage_still_leaves_a_playable_room(monkeypatch) -> None:
    """A database blip must not stop the process: the show lives in memory."""

    def unavailable():
        raise RuntimeError("database is down")

    monkeypatch.setattr("app.rooms.SessionLocal", unavailable)
    registry = RoomRegistry()
    await registry.load()
    assert registry.codes() == [DEFAULT_ROOM]


async def test_a_player_joins_a_room(client: AsyncClient) -> None:
    headers = await _player(client, "join-tester")
    response = await client.post(f"/api/rooms/{DEFAULT_ROOM}/join", headers=headers)
    assert response.status_code == 200
    assert response.json()["room"] == DEFAULT_ROOM

    async with SessionLocal() as session:
        player = (
            await session.execute(select(Player).where(Player.nickname == "join-tester"))
        ).scalar_one()
        assert player.room_code == DEFAULT_ROOM


async def test_joining_an_unknown_room_is_refused(client: AsyncClient) -> None:
    headers = await _player(client, "confused-player")
    response = await client.post("/api/rooms/nope/join", headers=headers)
    assert response.status_code == 404


async def test_an_admin_token_cannot_join_a_room(client: AsyncClient, admin_headers: dict) -> None:
    """The operator does not play, so an admin token is not a membership."""
    response = await client.post(f"/api/rooms/{DEFAULT_ROOM}/join", headers=admin_headers)
    assert response.status_code == 401


async def test_the_roster_lists_players_by_total(client: AsyncClient, admin_headers: dict) -> None:
    leader = await _player(client, "roster-leader")
    trailer = await _player(client, "roster-trailer")
    for headers in (leader, trailer):
        await client.post(f"/api/rooms/{DEFAULT_ROOM}/join", headers=headers)

    async with SessionLocal() as session:
        ids = {
            nickname: player_id
            for nickname, player_id in (
                await session.execute(
                    select(Player.nickname, Player.id).where(
                        Player.nickname.in_(["roster-leader", "roster-trailer"])
                    )
                )
            ).all()
        }
        session.add_all(
            [
                Score(player_id=ids["roster-leader"], hallucination_score=10, data_score=20),
                Score(player_id=ids["roster-trailer"], hallucination_score=-5, data_score=0),
            ]
        )
        await session.commit()

    response = await client.get(f"/api/rooms/{DEFAULT_ROOM}/players", headers=admin_headers)
    assert response.status_code == 200
    roster = [
        entry
        for entry in response.json()["players"]
        if entry["nickname"] in {"roster-leader", "roster-trailer"}
    ]
    assert [entry["nickname"] for entry in roster] == ["roster-leader", "roster-trailer"]
    assert roster[0]["total_score"] == 30
    assert roster[1]["total_score"] == -5


async def test_a_player_without_a_score_row_is_listed_as_zero(
    client: AsyncClient, display_headers: dict
) -> None:
    """Everyone is on the roster from the moment they join, score or not."""
    headers = await _player(client, "no-score-yet")
    await client.post(f"/api/rooms/{DEFAULT_ROOM}/join", headers=headers)

    response = await client.get(f"/api/rooms/{DEFAULT_ROOM}/players", headers=display_headers)
    entry = next(e for e in response.json()["players"] if e["nickname"] == "no-score-yet")
    assert entry["total_score"] == 0


async def test_a_player_from_another_room_is_not_listed(
    client: AsyncClient, admin_headers: dict
) -> None:
    headers = await _player(client, "elsewhere-player")
    await client.post(f"/api/rooms/{DEFAULT_ROOM}/join", headers=headers)
    async with SessionLocal() as session:
        await session.execute(
            update(Player).where(Player.nickname == "elsewhere-player").values(room_code=None)
        )
        await session.commit()

    response = await client.get(f"/api/rooms/{DEFAULT_ROOM}/players", headers=admin_headers)
    assert all(e["nickname"] != "elsewhere-player" for e in response.json()["players"])


async def test_the_roster_is_not_public(client: AsyncClient) -> None:
    """Names of everyone in the room are for the operator and the big screen."""
    response = await client.get(f"/api/rooms/{DEFAULT_ROOM}/players")
    assert response.status_code == 401


async def test_room_list_counts_players_separately_from_connections(
    client: AsyncClient, admin_headers: dict
) -> None:
    headers = await _player(client, "counted-player")
    await client.post(f"/api/rooms/{DEFAULT_ROOM}/join", headers=headers)

    response = await client.get("/api/rooms", headers=admin_headers)
    room = next(r for r in response.json()["rooms"] if r["code"] == DEFAULT_ROOM)
    assert room["players"] >= 1
    assert room["clients"] == 0


async def test_finishing_a_room_empties_its_members(
    client: AsyncClient, admin_headers: dict
) -> None:
    """A finished round is over: membership and team are released, but the
    player rows and scores stay behind for the record."""
    created = await client.post(
        "/api/rooms", json={"code": "finish-empty", "event": "hk_employee"}, headers=admin_headers
    )
    assert created.status_code == 201

    for name in ("F-One", "F-Two"):
        headers = await _player(client, name)
        joined = await client.post("/api/rooms/finish-empty/join", headers=headers)
        assert joined.status_code == 200

    # Give both players a team, as the sabotage assignment would.
    await client.post("/api/rooms/finish-empty/teams/assign", headers=admin_headers)

    async with SessionLocal() as session:
        members = (
            await session.execute(select(Player).where(Player.room_code == "finish-empty"))
        ).scalars().all()
        assert len(members) == 2
        assert all(member.team in ("red", "blue") for member in members)

    finished = await client.post("/api/rooms/finish-empty/finish", headers=admin_headers)
    assert finished.status_code == 200
    assert finished.json()["phase"] == "finished"

    async with SessionLocal() as session:
        members = (
            await session.execute(select(Player).where(Player.room_code == "finish-empty"))
        ).scalars().all()
        assert members == []
        # The players still exist, just with no room and no team.
        survivors = (
            await session.execute(
                select(Player).where(Player.nickname.in_(["F-One", "F-Two"]))
            )
        ).scalars().all()
        assert len(survivors) == 2
        assert all(p.room_code is None and p.team is None for p in survivors)


async def test_a_finished_room_is_closed_not_restartable(
    client: AsyncClient, admin_headers: dict
) -> None:
    """Finish closes the room; the next round is a new room, not a rewind."""
    await client.post("/api/rooms", json={"code": "finish-restart"}, headers=admin_headers)
    headers = await _player(client, "R-One")
    await client.post("/api/rooms/finish-restart/join", headers=headers)

    await client.post("/api/rooms/finish-restart/finish", headers=admin_headers)
    restarted = await client.post("/api/rooms/finish-restart/start", headers=admin_headers)
    assert restarted.status_code == 409

    # The participant record survives even though the room closed.
    async with SessionLocal() as session:
        from sqlalchemy import func, select

        from app.models import RoomParticipant
        count = await session.scalar(
            select(func.count())
            .select_from(RoomParticipant)
            .where(RoomParticipant.room_code == "finish-restart")
        )
        assert count == 1
