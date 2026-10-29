"""Player-reported scores: section mapping, idempotency, and refusals."""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.auth import ADMIN_SUBJECT, SCOPE_FULL, create_token
from app.database import SessionLocal
from app.flow import ROUND_FLOW
from app.main import app
from app.models import ScoreEvent
from app.players import SECTION_FIELDS

# Indices into ROUND_FLOW, named so a reordered flow breaks the test loudly
# rather than silently scoring the wrong section.
# Derived, not hardcoded: adding or removing an intro node used to break these
# with an index that pointed at whatever had taken the old slot.
from app.flow import ROUND_FLOW  # noqa: E402

_GAMES = [i for i, node in enumerate(ROUND_FLOW) if node["type"] == "game"]
_QUIZZES = [i for i, node in enumerate(ROUND_FLOW) if node["type"] == "quiz"]

HALLUCINATION_GAME = _GAMES[0]
DATA_SHADOW = _GAMES[1]
SABOTAGE = _GAMES[2]
QUIZ_ROUND_1 = _QUIZZES[0]
QUIZ_ROUND_2 = _QUIZZES[1]
ULTIMATE_QUIZ = _QUIZZES[2]
INTRO = 0


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer " + create_token(ADMIN_SUBJECT, SCOPE_FULL)}


async def _player(client: AsyncClient, nickname: str, room: str | None = None) -> dict[str, str]:
    response = await client.post(
        "/api/auth/player", json={"nickname": nickname, "event": "hk_employee"}
    )
    assert response.status_code == 200
    headers = {"Authorization": "Bearer " + response.json()["token"]}
    if room is not None:
        joined = await client.post(f"/api/rooms/{room}/join", headers=headers)
        assert joined.status_code == 200
    return headers


async def _room(client: AsyncClient, admin_headers: dict[str, str], code: str) -> None:
    created = await client.post("/api/rooms", json={"code": code}, headers=admin_headers)
    assert created.status_code == 201


async def _report(
    client: AsyncClient, headers: dict[str, str], room: str, node: int, points: int, question=None
):
    return await client.post(
        "/api/me/score",
        json={"room": room, "node_index": node, "question": question, "points": points},
        headers=headers,
    )


def test_every_flow_section_has_a_column() -> None:
    """The flow and the score columns must not drift apart."""
    declared = {node["score"] for node in ROUND_FLOW if "score" in node}
    assert declared == set(SECTION_FIELDS)
    assert all(section in SECTION_FIELDS for section in declared)


async def test_a_game_answer_lands_in_its_section(
    client: AsyncClient, admin_headers: dict
) -> None:
    await _room(client, admin_headers, "score-alpha")
    headers = await _player(client, "Scorer", room="score-alpha")

    response = await _report(client, headers, "score-alpha", HALLUCINATION_GAME, 20, question=0)
    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is True
    assert body["hallucination_score"] == 20
    assert body["total_score"] == 20

    listed = await client.get("/api/players?room=score-alpha", headers=admin_headers)
    assert listed.status_code == 200
    assert listed.json()["players"][0]["total_score"] == 20


async def test_the_quiz_after_a_game_shares_its_section(
    client: AsyncClient, admin_headers: dict
) -> None:
    """Hallucination + Quiz round 1 are one section, so the column accumulates."""
    await _room(client, admin_headers, "score-sections")
    headers = await _player(client, "Sectioned", room="score-sections")

    await _report(client, headers, "score-sections", HALLUCINATION_GAME, 20, question=0)
    quiz = await _report(client, headers, "score-sections", QUIZ_ROUND_1, 20, question=0)
    assert quiz.status_code == 200
    assert quiz.json()["hallucination_score"] == 40

    data = await _report(client, headers, "score-sections", DATA_SHADOW, 45, question=None)
    assert data.json()["data_score"] == 45
    assert data.json()["hallucination_score"] == 40

    quiz2 = await _report(client, headers, "score-sections", QUIZ_ROUND_2, 20, question=0)
    assert quiz2.json()["data_score"] == 65

    ultimate = await _report(client, headers, "score-sections", ULTIMATE_QUIZ, 20, question=0)
    assert ultimate.json()["ultimate_score"] == 20
    assert ultimate.json()["total_score"] == 40 + 65 + 20


async def test_a_replayed_report_does_not_pay_twice(
    client: AsyncClient, admin_headers: dict
) -> None:
    await _room(client, admin_headers, "score-twice")
    headers = await _player(client, "Retrier", room="score-twice")

    first = await _report(client, headers, "score-twice", HALLUCINATION_GAME, 20, question=0)
    second = await _report(client, headers, "score-twice", HALLUCINATION_GAME, 20, question=0)

    assert first.json()["applied"] is True
    assert second.json()["applied"] is False
    assert second.json()["hallucination_score"] == 20

    # A different question is a different event and does score.
    third = await _report(client, headers, "score-twice", HALLUCINATION_GAME, 20, question=1)
    assert third.json()["applied"] is True
    assert third.json()["hallucination_score"] == 40

    player_id = first.json()["player_id"]
    async with SessionLocal() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(ScoreEvent)
            .where(ScoreEvent.player_id == player_id)
        )
    assert count == 2


async def test_negative_points_are_accepted(client: AsyncClient, admin_headers: dict) -> None:
    """Doubt has a negative ceiling, so a wrong trust call must subtract."""
    await _room(client, admin_headers, "score-negative")
    headers = await _player(client, "Doubter", room="score-negative")

    response = await _report(client, headers, "score-negative", HALLUCINATION_GAME, -20, question=0)
    assert response.status_code == 200
    assert response.json()["hallucination_score"] == -20


async def test_a_node_without_a_section_is_refused(
    client: AsyncClient, admin_headers: dict
) -> None:
    await _room(client, admin_headers, "score-refuse")
    headers = await _player(client, "Refused", room="score-refuse")

    response = await _report(client, headers, "score-refuse", INTRO, 50, question=None)
    assert response.status_code == 422
    assert "does not score" in response.json()["detail"]


async def test_an_out_of_range_node_is_refused(
    client: AsyncClient, admin_headers: dict
) -> None:
    await _room(client, admin_headers, "score-range")
    headers = await _player(client, "Ranged", room="score-range")

    response = await _report(client, headers, "score-range", 99, 20, question=0)
    assert response.status_code == 422


async def test_a_player_outside_the_room_cannot_score(
    client: AsyncClient, admin_headers: dict
) -> None:
    await _room(client, admin_headers, "score-home")
    await _room(client, admin_headers, "score-other")
    headers = await _player(client, "Elsewhere", room="score-home")

    response = await _report(client, headers, "score-other", HALLUCINATION_GAME, 20, question=0)
    assert response.status_code == 409


async def test_an_unknown_room_is_refused(client: AsyncClient, admin_headers: dict) -> None:
    headers = await _player(client, "Lost")
    response = await _report(client, headers, "no-such-room", HALLUCINATION_GAME, 20, question=0)
    assert response.status_code == 404


async def test_an_admin_token_cannot_report(client: AsyncClient, admin_headers: dict) -> None:
    """The operator does not play; an admin token must not claim points."""
    await _room(client, admin_headers, "score-admin")
    response = await _report(client, admin_headers, "score-admin", HALLUCINATION_GAME, 20, question=0)
    assert response.status_code == 401


async def test_absurd_points_are_refused(client: AsyncClient, admin_headers: dict) -> None:
    await _room(client, admin_headers, "score-huge")
    headers = await _player(client, "Huge", room="score-huge")

    response = await _report(client, headers, "score-huge", HALLUCINATION_GAME, 10_000, question=0)
    assert response.status_code == 422
