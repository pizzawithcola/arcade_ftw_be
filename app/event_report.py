"""Per-event results: who came, how they scored, how many rounds ran.

Read-only, and deliberately kept apart from the game endpoints. Nothing here
writes, so an operator can look at the numbers while a show is running without
any risk to the room in front of the audience.

Membership is the player's own `event` label, the one they chose on the login
screen. That field is `NOT NULL`, validated against `app.events` on the way in
and unique together with the nickname, which is why it - and not a date window -
is the dimension every count here is scoped by.

Rounds are the exception: a room carries no event, so the number of rounds is
*derived* from who joined. A room is attributed to the event most of its
participants signed in for. Every room in the database so far is single-event,
so the derivation is clean; when `rooms.event` starts being written at creation
this becomes a plain `group by` and the `derived` flag can go.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import archive
from app.auth import require_display
from app.database import get_session
from app.events import EVENTS, EVENT_KEYS
from app.models import Player, Score

router = APIRouter(prefix="/events", tags=["events"])

LABELS = {event["key"]: event["label"] for event in EVENTS}
DATES = {event["key"]: event["dates"] for event in EVENTS}
FACTS = {event["key"]: event["facts"] for event in EVENTS}
SHORTS = {event["key"]: event["short"] for event in EVENTS}
EVENT_ORDER = [event["key"] for event in EVENTS]
MODULES = {event["key"]: event["modules"] for event in EVENTS}
TABS = {event["key"]: event["tab"] for event in EVENTS}
# The days the show actually ran, so the day chart plots the event and not the
# handful of sign-ups that straggle in around it (a rehearsal account, a test
# login on the following week).
WINDOWS = {event["key"]: (event["start"], event["end"]) for event in EVENTS}

# A pseudo-event for the report: every event at once. It is not selectable at
# login and never reaches `players.event`, so it is deliberately kept out of
# `EVENTS` and only accepted by the report endpoints.
ALL_KEY = "all"
ALL_LABEL = "All-time Summary"
ALL_TITLE = "Arcade 2.0 - Overall Summary"

# Both host cities are UTC+8, and a registration at 00:30 HKT belongs to that
# day, not to the previous one. Bucketing by a fixed offset keeps the report the
# same whatever timezone the operator's laptop is in.
REPORT_TZ = timezone(timedelta(hours=8))

# The four scored sections, in the order the show runs them, and what a perfect
# run of each is worth. The ceiling lives here so the report can print
# "324.5 / 600" without the frontend keeping its own copy of the rules.
GAME_COLUMNS = ("hallucination_score", "data_score", "sabotage_score", "ultimate_score")
GAME_KEYS = ("hallucination", "data", "sabotage", "ultimate")
GAME_MAX = {"hallucination": 600, "data": 800, "sabotage": 800, "ultimate": 600}


def _label(key: str) -> str:
    if key == ALL_KEY:
        return ALL_LABEL
    return LABELS.get(key, key)


def _title(key: str) -> str:
    """The line printed above a report: which arcade, which show, which days.

    The generation is part of the name because the two systems scored
    differently - a reader who mistakes 1.0's out-of-500 total for 2.0's
    out-of-2,800 one will draw the wrong conclusion from the whole page.
    """
    if key == ALL_KEY:
        return ALL_TITLE
    dates = DATES.get(key)
    label = _label(key)
    return f"Arcade 2.0 - {label} ({dates})" if dates else f"Arcade 2.0 - {label}"


def _checked(key: str) -> str:
    """Unknown events are refused rather than silently reported as empty, so a
    typo in the console cannot look like an event nobody attended.

    Archived events are accepted here because the console asks for them by the
    same key; the endpoints that need live rows check `archive.is_archived` and
    refuse, rather than quietly reporting an empty event.
    """
    if key == ALL_KEY or archive.is_archived(key):
        return key
    if key not in EVENT_KEYS:
        raise HTTPException(status_code=404, detail=f"unknown event: {key}")
    return key


def _round(value: float) -> float:
    return round(float(value), 1)


def _rows(rows) -> list[dict]:
    """Everything the report needs, off one query: one row per player of the
    event with their score row (absent when they never scored)."""
    return [
        {
            "nickname": player.nickname,
            "event": player.event,
            # What this player's own event could score. A total on its own is not
            # comparable between shows that ran different numbers of games.
            "max": EVENT_MAX.get(player.event, sum(GAME_MAX.values())),
            "created_at": player.created_at,
            # Having a score row at all is the record that this player answered
            # something. A player who answered and scored nothing still played,
            # which is why this is not `total > 0`.
            "played": score is not None,
            "total": score.total_score if score is not None else 0,
            "games": {
                key: (getattr(score, column) or 0) if score is not None else 0
                for key, column in zip(GAME_KEYS, GAME_COLUMNS)
            },
        }
        for player, score in rows
    ]


def _breakdown(rows: list[dict]) -> dict:
    """The all-time report's per-event cuts: one row per event, plus its games.

    Every live event appears even with no players, because a missing column and
    a column of dashes say different things: the first looks like a bug, the
    second says "this event happened and nobody here played that game".
    """
    by_event = []
    for event_key in EVENT_ORDER:
        mine = [row for row in rows if row["event"] == event_key]
        if not mine:
            by_event.append(
                {
                    "key": event_key,
                    "label": _label(event_key),
                    "short": SHORTS[event_key],
                    "players": 0,
                    "played": 0,
                    "games": {game: {"players": 0, "average": 0.0} for game in GAME_KEYS},
                }
            )
            continue
        by_event.append(
            {
                "key": event_key,
                "label": _label(event_key),
                "short": SHORTS[event_key],
                "players": len(mine),
                "played": len([row for row in mine if row["played"]]),
                "games": {
                    game: {
                        "players": _game_stats(mine, game)["players"],
                        "average": _game_stats(mine, game)["average"],
                    }
                    for game in GAME_KEYS
                },
            }
        )
    return {"by_event": by_event}


async def _player_rows(session: AsyncSession, key: str) -> list[dict]:
    """One query for every player of the event, or of every event for `all`.

    `all` is the same read with the label filter removed - it is a summary of
    what the database holds, not a separate aggregate to keep in sync. A person
    who signed in at two events is two rows here, because `players` is unique per
    (event, nickname) and the sign-in is the unit this page counts.
    """
    query = select(Player, Score).outerjoin(Score, Score.player_id == Player.id)
    if key != ALL_KEY:
        query = query.where(Player.event == key)
    rows = (await session.execute(query.order_by(Player.created_at, Player.id))).all()
    return _rows(rows)


async def _rounds(session: AsyncSession) -> dict[str, dict[str, int]]:
    """Rounds and participations per event, attributed by majority of joiners.

    `DISTINCT ON` picks the winning event per room; ties break on the event key so
    a 50/50 room always lands on the same side instead of flickering between
    reads.
    """
    result = await session.execute(
        text(
            """
            WITH votes AS (
                SELECT rp.room_code, p.event, count(*) AS people
                FROM room_participants rp
                JOIN players p ON p.id = rp.player_id
                GROUP BY rp.room_code, p.event
            ),
            best AS (
                SELECT DISTINCT ON (room_code) room_code, event, people
                FROM votes
                ORDER BY room_code, people DESC, event
            )
            SELECT event, count(*) AS rounds, sum(people) AS participations
            FROM best
            GROUP BY event
            """
        )
    )
    return {
        row.event: {"rounds": int(row.rounds), "participations": int(row.participations)}
        for row in result
    }


async def _unattributed_rooms(session: AsyncSession) -> int:
    """Rooms nobody joined. They belong to no event by any reading, and counting
    them or not changes what "how many rounds" means, so the number is published
    rather than folded in."""
    return int(
        await session.scalar(
            text(
                """
                SELECT count(*) FROM rooms r
                WHERE NOT EXISTS (
                    SELECT 1 FROM room_participants rp WHERE rp.room_code = r.code
                )
                """
            )
        )
        or 0
    )


def _registration_days(rows: list[dict], window: tuple[str, str] | None = None) -> list[dict]:
    """Sign-ups per day, and how many of that day's sign-ups went on to play.

    Both numbers in one bucket because they are read together: a tall bar with
    a short played count is a day people arrived and were never taken into a
    room, which is a different problem from a quiet day.

    `window` clips the chart to the days the show ran. Sign-ups outside it are
    still counted in the headcount - they exist - but a chart of an event on
    Nov. 2-3 that also shows a bar on Nov. 11 reads as an event that ran for ten
    days, which is the one thing this chart must not say.
    """
    start, end = window if window else ("", "9999-99-99")
    days: dict[str, dict[str, int]] = {}
    for row in rows:
        day = row["created_at"].astimezone(REPORT_TZ).strftime("%Y-%m-%d")
        if day < start or day > end:
            continue
        bucket = days.setdefault(day, {"count": 0, "played": 0})
        bucket["count"] += 1
        if row["played"]:
            bucket["played"] += 1
    return [{"date": day, **counts} for day, counts in sorted(days.items())]


def _event_max(key: str) -> int:
    """What a perfect run of that event was worth.

    The events did not run the same set of games - Singapore had no Sabotage - so
    a perfect night was 2,800 at one show and 2,000 at another. Every comparison
    across events has to divide by the total the player could actually reach.
    """
    return sum(GAME_MAX[game] for game in MODULES.get(key, ()))


EVENT_MAX = {event["key"]: _event_max(event["key"]) for event in EVENTS}


def _game_stats(rows: list[dict], game: str) -> dict:
    """Players, average and best for one game, over whatever rows are given.

    Shared by the event-wide table and by the per-event columns of the all-time
    report, so the two can never disagree about what an average means.
    """
    points = [row["games"][game] for row in rows if row["games"][game] > 0]
    return {
        "players": len(points),
        "average": _round(sum(points) / len(points)) if points else 0.0,
        "best": max(points) if points else 0,
        "max": GAME_MAX[game],
    }


# Bands of a *relative* score, as a percentage of what that player's event could
# have scored. Absolute bands would compare a three-game night to a four-game
# one and call the difference a result. 0 is its own band because "answered and
# scored nothing" is the finding, not the bottom of a range.
# Ten-point steps: the chart beside this list has one column per band, and ten
# columns read as a distribution where five read as a bar chart.
SCORE_BANDS: list[tuple[int, int]] = [
    (0, 0),
    (1, 10),
    (11, 20),
    (21, 30),
    (31, 40),
    (41, 50),
    (51, 60),
    (61, 70),
    (71, 80),
    (81, 90),
    (91, 100),
]


def _band_label(low: int, high: int) -> str:
    return "0" if low == high == 0 else f"{low}–{high}%"


def _score_distribution(rows: list[dict]) -> list[dict]:
    """How the room's scores spread, as a share of each player's own ceiling.

    A mean and a median cannot answer the question a room asks about itself -
    "did this separate anyone, or did everybody get the same?" - and two rooms
    with the same average can look nothing alike.

    The share, not the raw total: events ran different numbers of games, so a
    perfect score was 2,800 at Hong Kong and 2,000 in Singapore. Only players who
    played are counted - a sign-in who never answered is a different report - and
    any non-zero score lands above 0 even if it rounds to nothing.
    """
    bands = {label: 0 for label in (_band_label(low, high) for low, high in SCORE_BANDS)}
    for row in rows:
        if not row["played"]:
            continue
        ceiling = row["max"] or 1
        if row["total"] <= 0:
            bands["0"] += 1
            continue
        share = min(100, -(-row["total"] * 100 // ceiling))
        for low, high in SCORE_BANDS:
            if low <= share <= high:
                bands[_band_label(low, high)] += 1
                break
    return [{"label": label, "count": count} for label, count in bands.items()]


def _share(rows: list[dict], pick, mean: bool = False) -> float:
    """A score as a share of what that player's own event could score, as 0-100.

    Percentages rather than fractions because they are printed next to a score
    ("949.6 (34%)"), and a raw 0.339 next to 949.6 reads as a third figure.
    """
    if not rows:
        return 0.0
    shares = [pick(row) / (row["max"] or 1) * 100 for row in rows]
    value = sum(shares) / len(shares) if mean else max(shares)
    return round(min(value, 100.0), 1)


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def _summary(key: str, rows: list[dict], rounds: dict[str, dict[str, int]], unattributed: int) -> dict:
    totals = [row["total"] for row in rows if row["total"] > 0]
    games = {game: _game_stats(rows, game) for game in GAME_KEYS}
    played = len([row for row in rows if row["played"]])
    # `all` sums every event's rooms. The participant counts are already
    # per-room attributions, so adding them up does not double-count a round -
    # each room is attributed to exactly one event before it is counted.
    per_event = (
        {
            "rounds": sum(item["rounds"] for item in rounds.values()),
            "participations": sum(item["participations"] for item in rounds.values()),
        }
        if key == ALL_KEY
        else rounds.get(key, {"rounds": 0, "participations": 0})
    )
    return {
        "event": {
            "key": key,
            "label": _label(key),
            "tab": TABS.get(key, _label(key)),
            "title": _title(key),
            "facts": FACTS.get(key),
        },
        "players": len(rows),
        # Everyone who was in a room and answered something, including the ones
        # whose answers earned nothing. This is the number the room asks for:
        # "did they play?", not "did they score?".
        "played": played,
        "played_rate": round(played / len(rows), 3) if rows else 0.0,
        # Kept alongside: a player who scored nothing at all is a different
        # signal from one who never answered, and the gap between the two is
        # what says whether a round actually ran.
        "scored": len(totals),
        # A player who signed in and never answered is a different signal from a
        # player who did not turn up, so the rate is published, not just the count.
        "scored_rate": round(len(totals) / len(rows), 3) if rows else 0.0,
        "score": {
            "average": _round(sum(totals) / len(totals)) if totals else 0.0,
            "median": _median(totals),
            "best": max(totals) if totals else 0,
            "lowest": min(totals) if totals else 0,
            # What those totals were out of, and what share of it they represent.
            # `max` is null on the all-time summary, where every player came from
            # an event with its own ceiling - the shares are then the average of
            # each player's own share, which is the only reading that survives
            # adding a four-game show to a three-game one.
            "max": EVENT_MAX.get(key),
            "average_share": _share(
                [row for row in rows if row["total"] > 0], lambda row: row["total"], mean=True
            ),
            "best_share": _share(
                [row for row in rows if row["total"] > 0], lambda row: row["total"]
            ),
        },
        "games": games,
        "registrations_per_day": _registration_days(rows, WINDOWS.get(key)),
        "distribution": _score_distribution(rows),
        # `all` is the only report with more than one event in it, so it is the
        # only one that needs the breakdowns below. One event's report would be a
        # table of itself.
        **(_breakdown(rows) if key == ALL_KEY else {}),
        "rooms": {
            "rounds": per_event["rounds"],
            "participations": per_event["participations"],
            "average_per_round": _round(per_event["participations"] / per_event["rounds"])
            if per_event["rounds"]
            else 0.0,
            # True until `rooms.event` is written at creation: see the module docstring.
            "derived": True,
            "unattributed": unattributed,
        },
    }


@router.get("", summary="Every event, with its headcount and last sign-up")
async def list_events(
    _: object = Depends(require_display),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The picker's data: one row per event plus the all-time summary, so the
    report does not have to invent its first tab.

    `latest` is the event with the most recent sign-up. That is the honest answer
    to "which one were we just running", because a room carries no event and a
    player carries the one they chose for themselves. It deliberately ignores the
    summary row: `all` is not an event anyone signed in for.
    """
    rows = (
        await session.execute(
            select(Player.event, func.count(Player.id), func.max(Player.created_at)).group_by(
                Player.event
            )
        )
    ).all()
    seen = {event: {"players": int(players), "last": last} for event, players, last in rows}

    events = [
        {
            **event,
            "title": _title(event["key"]),
            "facts": event.get("facts"),
            "players": seen.get(event["key"], {}).get("players", 0),
            "last_registration": (
                seen[event["key"]]["last"].isoformat()
                if event["key"] in seen and seen[event["key"]]["last"] is not None
                else None
            ),
        }
        for event in EVENTS
    ]
    active = [event for event in events if event["last_registration"] is not None]
    latest = max(active, key=lambda event: event["last_registration"])["key"] if active else None
    # The summary is the first tab, and its headcount is the sum of the sign-ins
    # below it - the same number the report's own `players` card will print.
    summary = {
        "key": ALL_KEY,
        "label": ALL_LABEL,
        "tab": "All Arcade 2.0",
        "title": ALL_TITLE,
        "dates": None,
        "facts": None,
        "players": sum(event["players"] for event in events),
        "last_registration": max(
            (event["last_registration"] for event in active), default=None
        ),
    }
    # Archived events come first - they are the oldest, and they are the one tab
    # that is not this system's own history. `source` is what tells the console
    # not to compare them.
    archived = [archive.row(key) for key in archive.ARCHIVES]
    return {"events": [*archived, summary, *events], "latest": latest}


@router.get("/{key}/summary", summary="Headcount, scores and rounds for one event")
async def event_summary(
    key: str,
    _: object = Depends(require_display),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The numbers behind one event's report.

    Scoped by the player label, so nothing here depends on the rooms, the clock
    or the current show - it is the same answer before, during and after.
    """
    key = _checked(key)
    if archive.is_archived(key):
        raise HTTPException(
            status_code=404,
            detail=f"{key} is an archived event; read it from /events/{key}/archive",
        )
    rows = await _player_rows(session, key)
    return _summary(key, rows, await _rounds(session), await _unattributed_rooms(session))


@router.get("/{key}/archive", summary="One event as it stood on Arcade 1.0")
async def event_archive(
    key: str,
    _: object = Depends(require_display),
) -> dict:
    """An event from the previous system, read-only and on its own scale.

    A separate path rather than a flag on the live report: the two payloads do
    not share a shape, and a reader that had to branch on `source` before it
    could read a number would eventually branch wrong.
    """
    if not archive.is_archived(key):
        raise HTTPException(status_code=404, detail=f"not an archived event: {key}")
    return archive.report(key)


@router.get("/{key}/leaderboard", summary="Top players of one event")
async def event_leaderboard(
    key: str,
    limit: int = Query(default=10, ge=1, le=200),
    _: object = Depends(require_display),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The event's own board, best first.

    Ties break on nickname so the order is stable between reads - a board that
    reshuffles equal scores looks broken even when the numbers are right.

    `all` merges the same nickname across events: two sign-ins at two events are
    one person on an all-time board, and listing "Sam" twice with half a score
    each would read as two different Sams.
    """
    key = _checked(key)
    if archive.is_archived(key):
        raise HTTPException(
            status_code=404,
            detail=f"{key} is an archived event; its board ships with /events/{key}/archive",
        )
    rows = await _player_rows(session, key)
    if key == ALL_KEY:
        merged: dict[str, dict] = {}
        for row in rows:
            bucket = merged.setdefault(
                row["nickname"].lower(),
                {"nickname": row["nickname"], "total": 0, "games": dict.fromkeys(GAME_KEYS, 0)},
            )
            bucket["total"] += row["total"]
            for game in GAME_KEYS:
                bucket["games"][game] += row["games"][game]
        rows = list(merged.values())
    ranked = sorted(
        (row for row in rows if row["total"] > 0),
        key=lambda row: (-row["total"], row["nickname"].lower()),
    )
    return {
        "event": {"key": key, "label": _label(key)},
        "players": [
            {
                "rank": index + 1,
                "nickname": row["nickname"],
                "total_score": row["total"],
                **{game: row["games"][game] for game in GAME_KEYS},
            }
            for index, row in enumerate(ranked[:limit])
        ],
    }
