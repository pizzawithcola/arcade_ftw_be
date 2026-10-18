"""Room and player administration: create, read, rename, remove, and score edits."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth import ADMIN_SUBJECT, SCOPE_DISPLAY, create_token
from app.database import SessionLocal
from app.main import app
from app.models import Player, Room, Score
from app.rooms import DEFAULT_ROOM, rooms


@pytest.fixture
async def client():
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


async def _player_headers(client: AsyncClient, nickname: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/player", json={"nickname": nickname, "event": "hk_employee"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


# ─── rooms ────────────────────────────────────────────────────────────────────
async def test_a_new_room_is_playable_immediately(
    client: AsyncClient, admin_headers: dict, display_headers: dict
) -> None:
    """The engine has to exist by the time the caller gets its 201, or the URL it
    was just told to open would answer 404."""
    created = await client.post(
        "/api/rooms",
        json={"code": "crud-alpha", "event": "sg_employee", "label": "Alpha"},
        headers=admin_headers,
    )
    assert created.status_code == 201
    assert created.json()["code"] == "crud-alpha"
    assert created.json()["state"]["phase"] == "idle"

    state = await client.get("/api/rooms/crud-alpha/state")
    assert state.status_code == 200

    listed = await client.get("/api/rooms", headers=display_headers)
    assert "crud-alpha" in [room["code"] for room in listed.json()["rooms"]]


async def test_a_round_gets_the_next_generated_code(client: AsyncClient, admin_headers: dict) -> None:
    """Starting a round is one POST with no code to invent."""
    first = await client.post("/api/rooms", json={"label": "Round one"}, headers=admin_headers)
    assert first.status_code == 201
    assert first.json()["code"].startswith("round-")

    second = await client.post("/api/rooms", json={}, headers=admin_headers)
    assert second.status_code == 201
    assert int(second.json()["code"].split("-")[1]) == int(first.json()["code"].split("-")[1]) + 1


async def test_players_can_ask_which_room_is_current(client: AsyncClient, admin_headers: dict) -> None:
    """No token: a device has to find the room before it has any other right."""
    newest = (await client.post("/api/rooms", json={}, headers=admin_headers)).json()["code"]
    found = await client.get("/api/rooms/current")
    assert found.status_code == 200
    assert found.json()["code"] == newest
    assert found.json()["phase"] == "idle"


async def test_the_newest_room_is_the_current_one(
    client: AsyncClient, admin_headers: dict, display_headers: dict
) -> None:
    """The big screen follows `current`, so it has to be the latest round."""
    newest = (await client.post("/api/rooms", json={}, headers=admin_headers)).json()["code"]
    listed = await client.get("/api/rooms", headers=display_headers)
    assert listed.json()["current"] == newest
    assert listed.json()["rooms"][0]["code"] == newest


async def test_the_room_list_carries_the_row_metadata(
    client: AsyncClient, admin_headers: dict, display_headers: dict
) -> None:
    """The operator's room table shows label and event, so the list needs them."""
    await client.post(
        "/api/rooms",
        json={"code": "crud-meta", "label": "Metadata room", "event": "sg_fintech_week"},
        headers=admin_headers,
    )
    listed = await client.get("/api/rooms", headers=display_headers)
    entry = next(r for r in listed.json()["rooms"] if r["code"] == "crud-meta")
    assert entry["label"] == "Metadata room"
    assert entry["event"] == "sg_fintech_week"


async def test_a_duplicate_room_code_is_refused(
    client: AsyncClient, admin_headers: dict
) -> None:
    payload = {"code": "crud-dup"}
    assert (await client.post("/api/rooms", json=payload, headers=admin_headers)).status_code == 201
    assert (await client.post("/api/rooms", json=payload, headers=admin_headers)).status_code == 409


@pytest.mark.parametrize("code", ["Has Space", "UPPER", "-leading", "a", "x" * 33])
async def test_a_code_that_cannot_live_in_a_url_is_refused(
    client: AsyncClient, admin_headers: dict, code: str
) -> None:
    response = await client.post("/api/rooms", json={"code": code}, headers=admin_headers)
    assert response.status_code == 422


async def test_creating_a_room_needs_a_full_admin(client: AsyncClient) -> None:
    assert (await client.post("/api/rooms", json={"code": "crud-nope"})).status_code == 401
    display = {"Authorization": f"Bearer {create_token(ADMIN_SUBJECT, SCOPE_DISPLAY)}"}
    assert (
        await client.post("/api/rooms", json={"code": "crud-nope"}, headers=display)
    ).status_code == 401


async def test_a_room_can_be_relabelled(client: AsyncClient, admin_headers: dict) -> None:
    await client.post(
        "/api/rooms", json={"code": "crud-label", "label": "Before"}, headers=admin_headers
    )
    patched = await client.patch(
        "/api/rooms/crud-label",
        json={"label": "After", "event": "hk_fintech_week"},
        headers=admin_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["label"] == "After"
    assert patched.json()["event"] == "hk_fintech_week"

    unknown_event = await client.patch(
        "/api/rooms/crud-label", json={"event": "not_an_event"}, headers=admin_headers
    )
    assert unknown_event.status_code == 422


async def test_a_room_detail_is_for_signed_in_screens(
    client: AsyncClient, admin_headers: dict, display_headers: dict
) -> None:
    await client.post("/api/rooms", json={"code": "crud-detail"}, headers=admin_headers)
    assert (await client.get("/api/rooms/crud-detail")).status_code == 401
    shown = await client.get("/api/rooms/crud-detail", headers=display_headers)
    assert shown.status_code == 200
    assert shown.json()["state"]["phase"] == "idle"
    assert (await client.get("/api/rooms/crud-missing", headers=display_headers)).status_code == 404


async def test_deleting_a_room_releases_its_players(
    client: AsyncClient, admin_headers: dict
) -> None:
    """The show can end; the people in it are still people."""
    await client.post("/api/rooms", json={"code": "crud-doomed"}, headers=admin_headers)
    headers = await _player_headers(client, "doomed-player")
    await client.post("/api/rooms/crud-doomed/join", headers=headers)

    assert (
        await client.delete("/api/rooms/crud-doomed", headers=admin_headers)
    ).status_code == 204
    assert (await client.get("/api/rooms/crud-doomed/state")).status_code == 404

    async with SessionLocal() as session:
        player = (
            await session.execute(select(Player).where(Player.nickname == "doomed-player"))
        ).scalar_one()
        assert player.room_code is None
        assert await session.get(Room, "crud-doomed") is None


async def test_a_running_room_cannot_be_deleted(
    client: AsyncClient, admin_headers: dict
) -> None:
    await client.post("/api/rooms", json={"code": "crud-live"}, headers=admin_headers)
    await client.post("/api/rooms/crud-live/start", headers=admin_headers)

    refused = await client.delete("/api/rooms/crud-live", headers=admin_headers)
    assert refused.status_code == 409
    await rooms.get("crud-live").finish()
    assert (await client.delete("/api/rooms/crud-live", headers=admin_headers)).status_code == 204


# ─── players ──────────────────────────────────────────────────────────────────
async def test_an_admin_can_add_a_player_without_their_device(
    client: AsyncClient, admin_headers: dict
) -> None:
    await client.post("/api/rooms", json={"code": "crud-roster"}, headers=admin_headers)
    created = await client.post(
        "/api/rooms/crud-roster/players",
        json={"nickname": "Manually Added", "event": "hk_employee"},
        headers=admin_headers,
    )
    assert created.status_code == 201
    assert created.json()["room"] == "crud-roster"

    roster = await client.get("/api/rooms/crud-roster/players", headers=admin_headers)
    assert "Manually Added" in [entry["nickname"] for entry in roster.json()["players"]]


async def test_a_manually_added_nickname_is_numbered_when_taken(
    client: AsyncClient, admin_headers: dict
) -> None:
    await client.post("/api/rooms", json={"code": "crud-dupname"}, headers=admin_headers)
    for _ in range(2):
        await client.post(
            "/api/rooms/crud-dupname/players",
            json={"nickname": "Twin", "event": "hk_fintech_week"},
            headers=admin_headers,
        )
    names = [entry["nickname"] for entry in (
        await client.get("/api/rooms/crud-dupname/players", headers=admin_headers)
    ).json()["players"]]
    assert names == ["Twin", "Twin (2)"]


async def test_adding_a_player_needs_a_full_admin_and_a_real_room(
    client: AsyncClient, admin_headers: dict
) -> None:
    display = {"Authorization": f"Bearer {create_token(ADMIN_SUBJECT, SCOPE_DISPLAY)}"}
    body = {"nickname": "Nope", "event": "hk_employee"}
    assert (
        await client.post("/api/rooms/crud-roster/players", json=body, headers=display)
    ).status_code == 401
    assert (
        await client.post("/api/rooms/no-such-room/players", json=body, headers=admin_headers)
    ).status_code == 404


async def test_a_player_can_be_read_back_with_their_score(
    client: AsyncClient, admin_headers: dict, display_headers: dict
) -> None:
    headers = await _player_headers(client, "readable-player")
    player_id = (await client.get("/api/auth/me", headers=headers)).json()["player_id"]

    shown = await client.get(f"/api/players/{player_id}", headers=display_headers)
    assert shown.status_code == 200
    assert shown.json()["nickname"] == "readable-player"
    assert shown.json()["score"] is None
    assert shown.json()["total_score"] == 0

    assert (await client.get(f"/api/players/{player_id}")).status_code == 401
    assert (await client.get("/api/players/999999", headers=display_headers)).status_code == 404


async def test_a_player_can_be_renamed_and_moved(
    client: AsyncClient, admin_headers: dict
) -> None:
    headers = await _player_headers(client, "movable-player")
    player_id = (await client.get("/api/auth/me", headers=headers)).json()["player_id"]
    await client.post("/api/rooms", json={"code": "crud-move"}, headers=admin_headers)

    patched = await client.patch(
        f"/api/players/{player_id}",
        json={"nickname": "renamed-player", "room_code": "crud-move"},
        headers=admin_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["nickname"] == "renamed-player"
    assert patched.json()["room_code"] == "crud-move"

    # An explicit null takes them out of the room; omitting the field must not.
    await client.patch(f"/api/players/{player_id}", json={"event": "sg_employee"}, headers=admin_headers)
    still = await client.get(f"/api/players/{player_id}", headers=admin_headers)
    assert still.json()["room_code"] == "crud-move"
    assert still.json()["event"] == "sg_employee"

    cleared = await client.patch(
        f"/api/players/{player_id}", json={"room_code": None}, headers=admin_headers
    )
    assert cleared.json()["room_code"] is None


async def test_moving_a_player_to_a_room_that_does_not_exist_is_refused(
    client: AsyncClient, admin_headers: dict
) -> None:
    headers = await _player_headers(client, "roomless-player")
    player_id = (await client.get("/api/auth/me", headers=headers)).json()["player_id"]
    response = await client.patch(
        f"/api/players/{player_id}", json={"room_code": "ghost-room"}, headers=admin_headers
    )
    assert response.status_code == 404


async def test_a_rename_that_collides_is_refused(
    client: AsyncClient, admin_headers: dict
) -> None:
    first = await _player_headers(client, "collide-a")
    second = await _player_headers(client, "collide-b")
    second_id = (await client.get("/api/auth/me", headers=second)).json()["player_id"]
    first_id = (await client.get("/api/auth/me", headers=first)).json()["player_id"]

    response = await client.patch(
        f"/api/players/{second_id}", json={"nickname": "collide-a"}, headers=admin_headers
    )
    assert response.status_code == 409
    assert (await client.get(f"/api/players/{first_id}", headers=admin_headers)).status_code == 200


# ─── scores ───────────────────────────────────────────────────────────────────
async def _scored_player(
    client: AsyncClient, admin_headers: dict, nickname: str
) -> int:
    headers = await _player_headers(client, nickname)
    return (await client.get("/api/auth/me", headers=headers)).json()["player_id"]


async def test_setting_scores_creates_the_row_and_the_total(
    client: AsyncClient, admin_headers: dict
) -> None:
    player_id = await _scored_player(client, admin_headers, "score-setter")
    response = await client.put(
        f"/api/players/{player_id}/score",
        json={"hallucination_score": 20, "data_score": -5},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["hallucination_score"] == 20
    assert body["data_score"] == -5
    assert body["sabotage_score"] == 0
    assert body["total_score"] == 15

    # A later partial write leaves the components it did not mention alone.
    again = await client.put(
        f"/api/players/{player_id}/score",
        json={"ultimate_score": 10},
        headers=admin_headers,
    )
    assert again.json()["hallucination_score"] == 20
    assert again.json()["total_score"] == 25


async def test_scores_can_be_adjusted_by_a_delta(
    client: AsyncClient, admin_headers: dict
) -> None:
    player_id = await _scored_player(client, admin_headers, "score-adjuster")
    await client.put(
        f"/api/players/{player_id}/score", json={"data_score": 10}, headers=admin_headers
    )
    response = await client.post(
        f"/api/players/{player_id}/score/adjust",
        json={"data_score": 15, "sabotage_score": -10},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["data_score"] == 25
    assert response.json()["sabotage_score"] == -10
    assert response.json()["total_score"] == 15


async def test_a_total_cannot_be_written_directly(
    client: AsyncClient, admin_headers: dict
) -> None:
    """It is a generated column: the four components are the only inputs."""
    player_id = await _scored_player(client, admin_headers, "total-writer")
    response = await client.put(
        f"/api/players/{player_id}/score",
        json={"data_score": 5, "total_score": 9999},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["total_score"] == 5


async def test_writing_scores_needs_a_full_admin(
    client: AsyncClient, display_headers: dict
) -> None:
    player_id = await _scored_player(client, display_headers, "score-guarded")
    response = await client.put(
        f"/api/players/{player_id}/score", json={"data_score": 5}, headers=display_headers
    )
    assert response.status_code == 401
    assert (
        await client.put("/api/players/999999/score", json={"data_score": 5}, headers=display_headers)
    ).status_code == 401


async def test_deleting_a_player_deletes_their_score(
    client: AsyncClient, admin_headers: dict
) -> None:
    """`scores` is owned by the player, so nothing is left pointing at a ghost."""
    player_id = await _scored_player(client, admin_headers, "doomed-scorer")
    await client.put(
        f"/api/players/{player_id}/score", json={"data_score": 12}, headers=admin_headers
    )

    assert (await client.delete(f"/api/players/{player_id}", headers=admin_headers)).status_code == 204
    assert (await client.get(f"/api/players/{player_id}", headers=admin_headers)).status_code == 404

    async with SessionLocal() as session:
        assert await session.get(Score, player_id) is None
        assert await session.get(Player, player_id) is None


async def test_the_player_list_can_be_filtered(client: AsyncClient, admin_headers: dict) -> None:
    await client.post("/api/rooms", json={"code": "crud-filter"}, headers=admin_headers)
    headers = await _player_headers(client, "filtered-player")
    player_id = (await client.get("/api/auth/me", headers=headers)).json()["player_id"]
    await client.patch(
        f"/api/players/{player_id}", json={"room_code": "crud-filter"}, headers=admin_headers
    )

    response = await client.get("/api/players", params={"room": "crud-filter"}, headers=admin_headers)
    assert response.status_code == 200
    assert [entry["nickname"] for entry in response.json()["players"]] == ["filtered-player"]
