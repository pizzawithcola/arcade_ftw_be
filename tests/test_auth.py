"""Auth tokens and the endpoints that hand them out, plus the admin guard."""

import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import ADMIN_SUBJECT, create_token, decode_token
from app.config import settings
from app.engine import Phase, engine
from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    await engine.finish()


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {create_token(ADMIN_SUBJECT)}"}


# ─── tokens ───────────────────────────────────────────────────────────────────
def test_token_round_trip() -> None:
    assert decode_token(create_token("player:7")) == "player:7"


def test_a_tampered_token_is_rejected() -> None:
    body, signature = create_token(ADMIN_SUBJECT).split(".")
    # Swap the payload for an admin claim while keeping the old signature.
    forged = create_token("player:1").split(".")[0] + "." + signature
    assert decode_token(forged) is None
    assert decode_token(f"{body}.{signature[:-2]}xx") is None
    assert decode_token("nonsense") is None
    assert decode_token("") is None


def test_an_expired_token_is_rejected() -> None:
    original = settings.token_ttl_seconds
    try:
        settings.token_ttl_seconds = -1
        expired = create_token(ADMIN_SUBJECT)
    finally:
        settings.token_ttl_seconds = original
    assert decode_token(expired) is None
    assert time.time() > 0


# ─── login ────────────────────────────────────────────────────────────────────
async def test_a_nickname_always_maps_to_the_same_player(client: AsyncClient) -> None:
    payload = {"nickname": "repeat-player", "event": "hk_fintech_week"}
    first = await client.post("/api/auth/player", json=payload)
    second = await client.post("/api/auth/player", json=payload)

    assert first.status_code == 200
    assert first.json()["player_id"] == second.json()["player_id"]
    assert first.json()["nickname"] == "repeat-player"
    assert first.json()["event"] == "hk_fintech_week"
    assert decode_token(first.json()["token"]) == f"player:{first.json()['player_id']}"


async def test_the_same_nickname_at_two_events_is_two_players(client: AsyncClient) -> None:
    """Otherwise two different people who share a name share one scoreboard."""
    here = await client.post(
        "/api/auth/player", json={"nickname": "same-name", "event": "hk_employee"}
    )
    there = await client.post(
        "/api/auth/player", json={"nickname": "same-name", "event": "sg_employee"}
    )

    assert here.json()["player_id"] != there.json()["player_id"]


async def test_an_unknown_event_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/player", json={"nickname": "somebody", "event": "atlantis_fintech_week"}
    )
    assert response.status_code == 422


async def test_a_missing_event_is_refused(client: AsyncClient) -> None:
    assert (await client.post("/api/auth/player", json={"nickname": "somebody"})).status_code == 422


async def test_the_events_endpoint_lists_every_selectable_event(client: AsyncClient) -> None:
    response = await client.get("/api/auth/events")
    assert response.status_code == 200

    events = response.json()["events"]
    assert [event["key"] for event in events] == [
        "hk_fintech_week",
        "hk_employee",
        "sg_fintech_week",
        "sg_employee",
    ]
    # The dropdown needs something to show; the key alone would be unreadable.
    assert all(event["label"] for event in events)

    # Anything the endpoint offers must be one the login endpoint accepts.
    for event in events:
        accepted = await client.post(
            "/api/auth/player", json={"nickname": "probe", "event": event["key"]}
        )
        assert accepted.status_code == 200, event["key"]


async def test_a_blank_nickname_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/player", json={"nickname": "   ", "event": "hk_employee"}
    )
    assert response.status_code == 422


async def test_admin_login_checks_the_password(client: AsyncClient) -> None:
    wrong = await client.post(
        "/api/auth/admin", json={"username": settings.admin_username, "password": "not-it"}
    )
    assert wrong.status_code == 401

    right = await client.post(
        "/api/auth/admin",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert right.status_code == 200
    assert decode_token(right.json()["token"]) == ADMIN_SUBJECT


async def test_me_reports_who_holds_the_token(client: AsyncClient, admin_headers: dict) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401
    body = (await client.get("/api/auth/me", headers=admin_headers)).json()
    assert body == {"subject": ADMIN_SUBJECT, "is_admin": True}


# ─── the admin guard on the game controls ─────────────────────────────────────
async def test_controls_refuse_everyone_but_an_admin(client: AsyncClient) -> None:
    assert (await client.post("/api/game/start")).status_code == 401

    player = await client.post(
        "/api/auth/player", json={"nickname": "not-the-admin", "event": "hk_employee"}
    )
    player_headers = {"Authorization": f"Bearer {player.json()['token']}"}
    assert (await client.post("/api/game/start", headers=player_headers)).status_code == 401

    assert (await client.post("/api/game/start", headers={"Authorization": "Bearer junk"})).status_code == 401


async def test_state_is_public(client: AsyncClient) -> None:
    response = await client.get("/api/game/state")
    assert response.status_code == 200
    assert set(response.json()) >= {"phase", "index", "node", "remaining_ms", "server_time_ms"}


async def test_an_admin_can_drive_the_show(client: AsyncClient, admin_headers: dict) -> None:
    started = await client.post("/api/game/start", headers=admin_headers)
    assert started.status_code == 200
    assert started.json()["phase"] == Phase.PLAYING.value
    assert started.json()["remaining_ms"] > 0

    paused = await client.post("/api/game/pause", headers=admin_headers)
    assert paused.json()["phase"] == Phase.PAUSED.value

    assert (await client.post("/api/game/resume", headers=admin_headers)).json()["phase"] == "playing"

    jumped = await client.post("/api/game/go/2", headers=admin_headers)
    assert jumped.json()["index"] == 2

    assert (await client.post("/api/game/go/99", headers=admin_headers)).status_code == 422
    assert (await client.post("/api/game/go/-1", headers=admin_headers)).status_code == 422

    assert (await client.post("/api/game/finish", headers=admin_headers)).json()["phase"] == "finished"


async def test_an_admin_can_reset_the_show(client: AsyncClient, admin_headers: dict) -> None:
    await client.post("/api/game/start", headers=admin_headers)
    await client.post("/api/game/go/4", headers=admin_headers)

    reset = await client.post("/api/game/reset", headers=admin_headers)
    assert reset.status_code == 200
    body = reset.json()
    assert body["phase"] == Phase.IDLE.value
    assert body["index"] == 0
    assert body["remaining_ms"] == 0


async def test_reset_refuses_everyone_but_an_admin(client: AsyncClient) -> None:
    assert (await client.post("/api/game/reset")).status_code == 401


async def test_the_docs_live_under_the_api_prefix(client: AsyncClient) -> None:
    """They must sit under /api, the only path CloudFront proxies to the backend.

    At the root they reach the S3 origin instead and the SPA rewrite answers with
    index.html, so this silently serves the React app in production.
    """
    spec = await client.get("/api/openapi.json")
    assert spec.status_code == 200
    assert spec.headers["content-type"].startswith("application/json")
    assert "/api/game/reset" in spec.json()["paths"]

    for path in ("/api/docs", "/api/redoc"):
        page = await client.get(path)
        assert page.status_code == 200, path
        assert page.headers["content-type"].startswith("text/html"), path

    # The root paths must not quietly return something else.
    assert (await client.get("/openapi.json")).status_code == 404
