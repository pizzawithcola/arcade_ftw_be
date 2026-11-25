"""Per-event reports: the same numbers before, during and after a show.

The report is read-only and scoped by the player's own event label, which is
why it can be trusted while a room is running. These tests pin the two things
that would quietly go wrong: an event's report must not include another event's
players, and rounds must be attributed to the event its participants joined for.
"""

from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import ADMIN_SUBJECT, SCOPE_FULL, create_token
from app.event_report import REPORT_TZ
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
    # Each game carries its own ceiling, so the report can print "324.5 / 600"
    # without the frontend keeping a second copy of the scoring rules.
    assert body["games"]["hallucination"]["max"] == 600
    assert body["games"]["data"]["max"] == 800
    assert body["games"]["ultimate"]["max"] == 600
    assert body["score"]["best"] >= 300
    assert body["scored"] == before["scored"] + 1


async def test_the_day_chart_covers_the_show_and_not_the_sign_ups_around_it(
    client: AsyncClient, display_headers: dict, event_players: dict
):
    """The fixture signs players up today, and today is not when Hong Kong
    Employee Week ran. Those rows still count towards the headcount - they exist
    - but they must not put a bar on a chart of a three-day event."""
    body = await _summary(client, display_headers, "hk_employee")
    before = event_players["before"]

    assert body["players"] == before["players"] + 3, 'the headcount still counts them'
    today = datetime.now(REPORT_TZ).strftime("%Y-%m-%d")
    assert today not in {day["date"] for day in body["registrations_per_day"]}
    # And every bucket that is left names a day inside the window.
    for day in body["registrations_per_day"]:
        assert "2026-11-09" <= day["date"] <= "2026-11-11", day["date"]


async def test_registrations_are_bucketed_by_host_time(
    client: AsyncClient, display_headers: dict, event_players: dict
):
    """A bucket is one host-side calendar day. `all` has no window of its own, so
    it is the report that still charts whatever arrived."""
    body = await _summary(client, display_headers, "all")
    today_row = [
        day for day in body["registrations_per_day"]
        if day["date"] == datetime.now(REPORT_TZ).strftime("%Y-%m-%d")
    ]
    assert today_row, 'todays sign-ups should be charted on the all-time summary'
    assert today_row[0]["count"] >= 3
    # Bucketed on the host's calendar, so a bucket names a plain date.
    assert len(today_row[0]["date"]) == 10 and today_row[0]["date"][4] == "-"


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


async def test_the_all_time_summary_adds_every_event_together(
    client: AsyncClient, display_headers: dict, event_players: dict
):
    """`all` is the same read with the label filter removed, so it has to agree
    with the parts: a summary that drifts from the events it summarises is worse
    than no summary at all."""
    keys = ("hk_fintech_week", "hk_employee", "sg_fintech_week")
    parts = [await _summary(client, display_headers, key) for key in keys]
    whole = await _summary(client, display_headers, "all")

    assert whole["event"]["key"] == "all"
    assert whole["event"]["label"] == "All-time Summary"
    # Sign-ins add up exactly: the same three rows the fixture just created are
    # counted once each, in their own event and again here.
    assert whole["players"] == sum(part["players"] for part in parts)
    assert whole["played"] == sum(part["played"] for part in parts)
    assert whole["scored"] == sum(part["scored"] for part in parts)
    # Each room is attributed to exactly one event before it is counted, so the
    # rounds can be added without double-counting a show.
    assert whole["rooms"]["rounds"] == sum(part["rooms"]["rounds"] for part in parts)
    assert whole["rooms"]["participations"] == sum(
        part["rooms"]["participations"] for part in parts
    )
    # Still a summary of one board: the best player in the world is the best
    # player of some event.
    assert whole["score"]["best"] == max(part["score"]["best"] for part in parts)


async def test_the_all_time_summary_cuts_itself_per_event(
    client: AsyncClient, display_headers: dict, event_players: dict
):
    """The all-time report is the only one that has to break itself down: one
    report per event would be four pages, and a Games column that says "Total"
    without saying which event it came from is not a comparison."""
    whole = await _summary(client, display_headers, "all")
    assert [row["key"] for row in whole["by_event"]] == [
        "hk_fintech_week",
        "hk_employee",
        "sg_fintech_week",
    ]
    employee = next(row for row in whole["by_event"] if row["key"] == "hk_employee")
    own = await _summary(client, display_headers, "hk_employee")
    # The column and the page it summarises must agree, or the table teaches
    # something the detail page contradicts.
    assert employee["players"] == own["players"]
    assert employee["played"] == own["played"]
    assert employee["games"]["hallucination"]["average"] == own["games"]["hallucination"]["average"]
    # Short names are what the narrow table headers use.
    assert [row["short"] for row in whole["by_event"]] == ["HK Fintech", "HK Employee", "SG Fintech"]
    # A single event's report has no breakdown of itself.
    assert "by_event" not in own


async def test_the_all_time_distribution_counts_who_played(
    client: AsyncClient, display_headers: dict, event_players: dict
):
    """Bands, not just an average: two rooms can share a mean and look nothing
    alike. Only players who played are counted, and 0 is its own band."""
    whole = await _summary(client, display_headers, "all")
    bands = {band["label"]: band["count"] for band in whole["distribution"]}
    assert list(bands) == ["0", "1–20%", "21–40%", "41–60%", "61–80%", "81–100%"]
    assert sum(bands.values()) == whole["played"], 'every player who played lands in one band'


async def test_the_distribution_is_a_share_of_what_each_event_could_score(
    client: AsyncClient, display_headers: dict
):
    """Hong Kong runs four games, Singapore three. A 2,000 in Singapore is a
    perfect night; the same total at Hong Kong is 71%. Absolute bands would call
    both "2,001-2,400" and hide the only thing worth knowing."""
    good = await _player(client, "Rep-Band-Hk", "hk_employee")
    perfect = await _player(client, "Rep-Band-Sg", "sg_fintech_week")
    try:
        # 2,000 of 2,800 at Hong Kong, 2,000 of 2,000 in Singapore.
        await _score(client, good, hallucination_score=600, data_score=800, sabotage_score=600)
        await _score(client, perfect, hallucination_score=600, data_score=800, ultimate_score=600)

        whole = await _summary(client, display_headers, "all")
        bands = {band["label"]: band["count"] for band in whole["distribution"]}
        assert bands["81–100%"] >= 1, 'a perfect Singapore night is 100% of its ceiling'
        assert bands["61–80%"] >= 1, '2,000 of 2,800 belongs in the 61-80 band'

        # The ceilings themselves are derived from the games each event ran.
        from app.event_report import EVENT_MAX
        assert EVENT_MAX["hk_employee"] == 2800
        assert EVENT_MAX["sg_fintech_week"] == 2000
    finally:
        await client.delete(f"/api/players/{good}", headers=ADMIN)
        await client.delete(f"/api/players/{perfect}", headers=ADMIN)


async def test_the_all_time_board_merges_one_person_across_events(
    client: AsyncClient, display_headers: dict
):
    """Two sign-ins at two events are one person on an all-time board. Listing
    the name twice with half the score each would read as two different people."""
    first = await _player(client, "Rep-Same-Person", "hk_employee")
    second = await _player(client, "Rep-Same-Person", "sg_fintech_week")
    try:
        await _score(client, first, hallucination_score=100)
        await _score(client, second, hallucination_score=400, data_score=200)

        board = (
            await client.get("/api/events/all/leaderboard?limit=200", headers=display_headers)
        ).json()
        rows = [row for row in board["players"] if row["nickname"] == "Rep-Same-Person"]
        assert len(rows) == 1, "the same nickname should be one person all-time"
        assert rows[0]["total_score"] == 700
        assert rows[0]["hallucination"] == 500
        assert rows[0]["data"] == 200

        # The per-event boards still keep the two sign-ins apart, which is what
        # makes the merge above a decision rather than a side effect.
        one = (
            await client.get(
                "/api/events/hk_employee/leaderboard?limit=200", headers=display_headers
            )
        ).json()
        assert [row["total_score"] for row in one["players"] if row["nickname"] == "Rep-Same-Person"] == [100]
    finally:
        await client.delete(f"/api/players/{first}", headers=ADMIN)
        await client.delete(f"/api/players/{second}", headers=ADMIN)


async def test_an_archived_event_is_read_from_its_own_endpoint(
    client: AsyncClient, display_headers: dict
):
    """Arcade 1.0 scored five games out of 100. Those numbers are shown as they
    were, on their own page, and never mixed into a 2.0 report."""
    response = await client.get("/api/events/uae_ai_connect/archive", headers=display_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["label"] == "Arcade 1.0 - UAE AI Connect"
    assert body["source"] == "arcade-1.0"
    assert body["headline"]["participants"] == 93
    assert body["headline"]["active_players"] == 69
    assert body["headline"]["rounds"] == 29
    # Five games on the old scale, each with 100 as its ceiling.
    assert [game["key"] for game in body["games"]] == ["game1", "game2", "game3", "game4", "game5"]
    assert all(game["max"] == 100 for game in body["games"])
    # The export only carries a top ten, and the payload says so.
    assert len(body["leaderboard"]) == 10
    assert body["leaderboard"][0]["rank"] == 1
    assert body["leaderboard_note"]
    # The line above the numbers names the shape of that show.
    assert body["tagline"] == (
        "Arcade 1.0 · AI Cyber Arcade for UAE AI Connect - "
        "2 rooms · 2 days · 5 games · 30 mins per round"
    )


async def test_an_archived_event_is_not_reportable_as_a_live_one(
    client: AsyncClient, display_headers: dict
):
    """The live endpoints refuse an archived key instead of reporting an event
    with no players, which would read as an event nobody attended."""
    for path in ("summary", "leaderboard"):
        response = await client.get(
            f"/api/events/uae_ai_connect/{path}", headers=display_headers
        )
        assert response.status_code == 404, path
        assert "archived" in response.json()["detail"]


async def test_the_archive_endpoint_refuses_a_live_event(client: AsyncClient, display_headers: dict):
    response = await client.get("/api/events/hk_employee/archive", headers=display_headers)
    assert response.status_code == 404


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
    assert keys == [
        "uae_ai_connect",
        "all",
        "hk_fintech_week",
        "hk_employee",
        "sg_fintech_week",
    ]
    # Every tab carries the line printed above its report: which arcade, which
    # show, which days. The generation is in the name because the two systems
    # score on different scales.
    titles = {event["key"]: event["title"] for event in body["events"]}
    assert titles["uae_ai_connect"] == "Arcade 1.0 - UAE AI Connect (Sep. 29-30)"
    assert titles["all"] == "Arcade 2.0 - Overall Summary"
    assert titles["hk_fintech_week"] == "Arcade 2.0 - Hong Kong Fintech Week (Nov. 2-3)"
    assert titles["hk_employee"] == "Arcade 2.0 - Hong Kong Employee Week (Nov. 9-11)"
    assert titles["sg_fintech_week"] == "Arcade 2.0 - Singapore Fintech Festival (Nov. 18-20)"
    # ...and the line under it: how the show was laid out, as the organisers
    # described it. Facts, not something derived from the rows.
    facts = {event["key"]: event.get("facts") for event in body["events"]}
    assert facts["sg_fintech_week"] == "1 booth · 3 days · 3 games · 5 mins per round"
    assert facts["hk_employee"] == "1 booth · 3 days · 4 games · 10 mins per round"
    assert facts["hk_fintech_week"] == "1 booth · 3 hours · 4 games · 10 mins per round"
    # The summary has no layout of its own - it is not a show.
    assert facts["all"] is None
    # The archived event is labelled as such, so nothing downstream treats it as
    # a live event with the same scoring scale.
    assert body["events"][0]["source"] == "arcade-1.0"
    assert "source" not in body["events"][1]
    # The summary's headcount is the sum of the sign-ins it summarises - not a
    # fourth event somebody could pick at login.
    summary = next(event for event in body["events"] if event["key"] == "all")
    assert summary["label"] == "All-time Summary"
    live = [event for event in body["events"] if event["key"] != "all" and "source" not in event]
    assert summary["players"] == sum(event["players"] for event in live)
    # ...and the archive is not one of them: its 93 players were scored by
    # another system, so they never reach this summary.
    assert body["events"][0]["players"] == 93
    # These players were created a moment ago, in both events, so the newest
    # sign-up is one of them rather than an event that only has old rows.
    assert body["latest"] in ("hk_fintech_week", "hk_employee")
    for event in body["events"]:
        assert isinstance(event["players"], int)
        assert "label" in event


async def test_the_event_list_needs_a_token(client: AsyncClient):
    response = await client.get("/api/events")
    assert response.status_code == 401
