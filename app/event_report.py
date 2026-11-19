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

from app.auth import require_display
from app.database import get_session
from app.events import EVENTS, EVENT_KEYS
from app.models import Player, Score

router = APIRouter(prefix="/events", tags=["events"])

LABELS = {event["key"]: event["label"] for event in EVENTS}

# Both host cities are UTC+8, and a registration at 00:30 HKT belongs to that
# day, not to the previous one. Bucketing by a fixed offset keeps the report the
# same whatever timezone the operator's laptop is in.
REPORT_TZ = timezone(timedelta(hours=8))

# The four scored sections, in the order the show runs them.
GAME_COLUMNS = ("hallucination_score", "data_score", "sabotage_score", "ultimate_score")
GAME_KEYS = ("hallucination", "data", "sabotage", "ultimate")


def _label(key: str) -> str:
    return LABELS.get(key, key)


def _checked(key: str) -> str:
    """Unknown events are refused rather than silently reported as empty, so a
    typo in the console cannot look like an event nobody attended."""
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


async def _player_rows(session: AsyncSession, key: str) -> list[dict]:
    rows = (
        await session.execute(
            select(Player, Score)
            .outerjoin(Score, Score.player_id == Player.id)
            .where(Player.event == key)
            .order_by(Player.created_at, Player.id)
        )
    ).all()
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


def _registration_days(rows: list[dict]) -> list[dict]:
    """Sign-ups per day, and how many of that day's sign-ups went on to play.

    Both numbers in one bucket because they are read together: a tall bar with
    a short played count is a day people arrived and were never taken into a
    room, which is a different problem from a quiet day.
    """
    days: dict[str, dict[str, int]] = {}
    for row in rows:
        day = row["created_at"].astimezone(REPORT_TZ).strftime("%Y-%m-%d")
        bucket = days.setdefault(day, {"count": 0, "played": 0})
        bucket["count"] += 1
        if row["played"]:
            bucket["played"] += 1
    return [{"date": day, **counts} for day, counts in sorted(days.items())]


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
    games = {}
    for game in GAME_KEYS:
        points = [row["games"][game] for row in rows if row["games"][game] > 0]
        games[game] = {
            "players": len(points),
            "average": _round(sum(points) / len(points)) if points else 0.0,
            "best": max(points) if points else 0,
        }
    played = len([row for row in rows if row["played"]])
    per_event = rounds.get(key, {"rounds": 0, "participations": 0})
    return {
        "event": {"key": key, "label": _label(key)},
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
        },
        "games": games,
        "registrations_per_day": _registration_days(rows),
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
    """The picker's data: three rows, so the console can open on the event that
    just finished instead of guessing.

    `latest` is the event with the most recent sign-up. That is the honest answer
    to "which one were we just running", because a room carries no event and a
    player carries the one they chose for themselves.
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
    return {"events": events, "latest": latest}


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
    rows = await _player_rows(session, key)
    return _summary(key, rows, await _rounds(session), await _unattributed_rooms(session))


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
    """
    key = _checked(key)
    rows = [row for row in await _player_rows(session, key) if row["total"] > 0]
    rows.sort(key=lambda row: (-row["total"], row["nickname"].lower()))
    return {
        "event": {"key": key, "label": _label(key)},
        "players": [
            {
                "rank": index + 1,
                "nickname": row["nickname"],
                "total_score": row["total"],
                **{game: row["games"][game] for game in GAME_KEYS},
            }
            for index, row in enumerate(rows[:limit])
        ],
    }
