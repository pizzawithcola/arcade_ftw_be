"""Per-event reports: the same numbers before, during and after a show.

The report is read-only and scoped by the player's own event label, which is
why it can be trusted while a room is running. These tests pin the two things
that would quietly go wrong: an event's report must not include another event's
players, and rounds must be attributed to the event its participants joined for.
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


@pytest.fixture
def display_headers() -> dict[str, str]:
    return {"Authorization": "Bearer " + create_token(ADMIN_SUBJECT, "display")}


async def _player(client: AsyncClient, nickname: str, event: str) -> int:
    response = await client.post("/api/auth/player", json={"nickname": nickname, "event": event})
    assert response.status_code == 200
    return response.json()["player_id"]


async def _score(client: AsyncClient, player_id: int, **parts: int) -> None:
    response = await client.put(f"/api/players/{player_id}/score", json=parts, headers=ADMIN)
    assert response.status_code == 200


async def _summary(client: AsyncClient, headers: dict, key: str) -> dict:
    response = await client.get(f"/api/events/{key}/summary", headers=headers)
    assert response.status_code == 200
    return response.json()


@pytest.fixture
async def event_players(client: AsyncClient, display_headers: dict):
    """One scored and one unscored player in each of two events.

    Two events on purpose: every assertion below would also pass if the report
    ignored the scope and simply counted everyone, which is the bug worth
    catching.

    The numbers are asserted as *deltas* against a snapshot taken first. The
    suite shares one database and earlier files leave players behind, so an
    absolute count would be measuring them rather than this report.
    """
    before = await _summary(client, display_headers, "hk_employee")
    also_before = await _summary(client, display_headers, "hk_fintech_week")
    scored = await _player(client, "Rep-Scored-Emp", "hk_employee")
    idle = await _player(client, "Rep-Idle-Emp", "hk_employee")
    # Answered and earned nothing: a zero row is the proof of having played.
    pointless = await _player(client, "Rep-Zero-Emp", "hk_employee")
    other = await _player(client, "Rep-Scored-Ftw", "hk_fintech_week")
    await _score(client, scored, hallucination_score=100, data_score=200)
    await _score(client, pointless, hallucination_score=0, data_score=0)
    await _score(client, other, hallucination_score=500, data_score=500)
    yield {
        "scored": scored,
        "idle": idle,
        "pointless": pointless,
        "other": other,
        "before": before,
        "also_before": also_before,
    }
    for player_id in (scored, idle, pointless, other):
        await client.delete(f"/api/players/{player_id}", headers=ADMIN)


async def test_an_event_reports_only_its_own_players(
    client: AsyncClient, display_headers: dict, event_players: dict
):
    body = await _summary(client, display_headers, "hk_employee")
    before = event_players["before"]
    assert body["event"]["key"] == "hk_employee"
    # Both of this event's players, and neither of the other event's.
    assert body["players"] == before["players"] + 3
    assert body["scored"] == before["scored"] + 1
    # The other event did grow, so the scope is doing something rather than
    # reporting a database that never changed.
    other = await _summary(client, display_headers, "hk_fintech_week")
    assert other["players"] == event_players["also_before"]["players"] + 1


async def test_the_summary_counts_scores_and_games(
    client: AsyncClient, display_headers: dict, event_players: dict
):
    body = await _summary(client, display_headers, "hk_employee")
    before = event_players["before"]
    # The scored player's 100 + 200 lands in the games it was earned in; the idle
    # one moves the headcount and nothing else.
    assert body["games"]["hallucination"]["players"] == before["games"]["hallucination"]["players"] + 1
    assert body["games"]["data"]["players"] == before["games"]["data"]["players"] + 1
    assert body["games"]["sabotage"] == before["games"]["sabotage"]
    assert body["score"]["best"] >= 300
    assert body["scored"] == before["scored"] + 1


async def test_registrations_are_bucketed_by_host_time(
    client: AsyncClient, display_headers: dict, event_players: dict
):
    body = await _summary(client, display_headers, "hk_employee")
    before = event_players["before"]
    today = body["registrations_per_day"][-1]
    earlier = {day["date"]: day["count"] for day in before["registrations_per_day"]}
    earlier_played = {day["date"]: day["played"] for day in before["registrations_per_day"]}
    assert today["count"] == earlier.get(today["date"], 0) + 3
    # Of the three new sign-ups, two played: the one who scored and the one who
    # answered and earned nothing. Having a score row is what "played" means,
    # not having points on it.
    assert today["played"] == earlier_played.get(today["date"], 0) + 2
    # Bucketed on the host's calendar, so the newest bucket is a plain date.
    assert len(today["date"]) == 10 and today["date"][4] == "-"


async def test_played_counts_an_answer_that_earned_nothing(
    client: AsyncClient, display_headers: dict, event_players: dict
):
    """A player who answered and scored zero played. A player who never
    answered did not. They are different facts and the report keeps both."""
    body = await _summary(client, display_headers, "hk_employee")
    before = event_players["before"]
    # Three sign-ups: one scored, one answered for nothing, one never answered.
    assert body["players"] == before["players"] + 3
    # Two of them played; only one of those scored.
    assert body["played"] == before["played"] + 2
    assert body["scored"] == before["scored"] + 1
    # And the rate is played over registered, not scored over registered.
    assert body["played"] > body["scored"]


async def test_rounds_are_attributed_to_the_event_the_room_was_played_in(
    client: AsyncClient, display_headers: dict, event_players: dict
):
    """Rooms carry no event, so the count comes from who joined: two players of
    one event in one room is one round of that event, not half of each."""
    before = await _summary(client, display_headers, "hk_fintech_week")
    other_before = await _summary(client, display_headers, "hk_employee")
    created = await client.post("/api/rooms", json={"code": "REPORT-ROUND"}, headers=ADMIN)
    assert created.status_code == 201
    try:
        for nickname in ("Rep-Join-One", "Rep-Join-Two"):
            login = await client.post(
                "/api/auth/player", json={"nickname": nickname, "event": "hk_fintech_week"}
            )
            token = login.json()["token"]
            joined = await client.post(
                "/api/rooms/REPORT-ROUND/join", headers={"Authorization": "Bearer " + token}
            )
            assert joined.status_code == 200

        body = await _summary(client, display_headers, "hk_fintech_week")
        assert body["rooms"]["rounds"] == before["rooms"]["rounds"] + 1
        assert body["rooms"]["participations"] == before["rooms"]["participations"] + 2
        # The flag is what tells the console the number is inferred, not stored.
        assert body["rooms"]["derived"] is True

        # The room was played by one event only, so the other one's count is
        # untouched - that is the attribution the derivation exists for.
        other = await _summary(client, display_headers, "hk_employee")
        assert other["rooms"]["rounds"] == other_before["rooms"]["rounds"]
    finally:
        await client.post("/api/rooms/REPORT-ROUND/finish", headers=ADMIN)
        await client.delete("/api/rooms/REPORT-ROUND", headers=ADMIN)
        listed = (await client.get("/api/players", headers=ADMIN)).json()["players"]
        for player in listed:
            if player["nickname"] in ("Rep-Join-One", "Rep-Join-Two"):
                await client.delete(f"/api/players/{player['player_id']}", headers=ADMIN)


async def test_the_event_leaderboard_ranks_its_own_players(
    client: AsyncClient, display_headers: dict, event_players: dict
):
    body = (
        await client.get("/api/events/hk_employee/leaderboard?limit=200", headers=display_headers)
    ).json()
    names = [row["nickname"] for row in body["players"]]
    # Only players who scored appear: a zero would sit on a board nobody is on.
    assert "Rep-Idle-Emp" not in names
    assert "Rep-Scored-Ftw" not in names, "another event's player leaked onto the board"
    me = next(row for row in body["players"] if row["nickname"] == "Rep-Scored-Emp")
    assert me["total_score"] == 300
    assert me["hallucination"] == 100
    assert me["data"] == 200
    # Ranks are the board's own order, best first.
    scores = [row["total_score"] for row in body["players"]]
    assert scores == sorted(scores, reverse=True)
    assert [row["rank"] for row in body["players"]] == list(range(1, len(scores) + 1))


async def test_an_unknown_event_is_refused(client: AsyncClient, display_headers: dict):
    for path in ("summary", "leaderboard"):
        response = await client.get(f"/api/events/nope/{path}", headers=display_headers)
        assert response.status_code == 404, path


async def test_the_report_needs_a_token(client: AsyncClient):
    response = await client.get("/api/events/hk_employee/summary")
    assert response.status_code == 401


async def test_the_event_list_names_the_one_that_just_finished(
    client: AsyncClient, display_headers: dict, event_players: dict
):
    """The console opens on the event with the newest sign-up, because nothing
    in the data marks a room - or a run - as current."""
    response = await client.get("/api/events", headers=display_headers)
    assert response.status_code == 200
    body = response.json()
    keys = [event["key"] for event in body["events"]]
    assert keys == ["hk_fintech_week", "hk_employee", "sg_fintech_week"]
    # These players were created a moment ago, in both events, so the newest
    # sign-up is one of them rather than an event that only has old rows.
    assert body["latest"] in ("hk_fintech_week", "hk_employee")
    for event in body["events"]:
        assert isinstance(event["players"], int)
        assert "label" in event


async def test_the_event_list_needs_a_token(client: AsyncClient):
    response = await client.get("/api/events")
    assert response.status_code == 401
