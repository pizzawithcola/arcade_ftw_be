"""Taking reported scores back off the board.

A score event is the record of one answer, keyed by `room:node:question`, and a
repeat is ignored so a retry cannot pay twice. That is right for a retry and
wrong for a rehearsal: when the operator jumps back to a node, or rewinds the
show to the top, the same players answer the same questions again, and every one
of those reports is a repeat of a key that already exists. Without this module
the second attempt would silently change nothing, and the board would keep the
first attempt's score forever.

So a (re)started node first gives its points back: every event under that key is
deleted and its points are subtracted from the player's section total. The node
then scores into a clean slate, which is what "restart this round" has to mean
for a show that is rehearsed before it is performed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Score, ScoreEvent
from app.players import SECTION_FIELDS


async def clear_node_scores(
    session: AsyncSession, room: str, node_index: int, flow: list[dict]
) -> int:
    """Give back every point scored on one node. Returns how many events went.

    Only that node's key is touched: replaying the Hallucination round must not
    disturb a Data Shadow score from a minute ago.
    """
    section = flow[node_index].get("score") if 0 <= node_index < len(flow) else None
    column = SECTION_FIELDS.get(section or "")
    if column is None:  # an unscored node has nothing to give back
        return 0
    return await _drop(session, f"{room}:{node_index}:", lambda _key: column)


async def clear_room_scores(session: AsyncSession, room: str, flow: list[dict]) -> int:
    """Give back the whole board for a room - what "start again from the top" means."""
    columns = {
        f"{room}:{index}:": SECTION_FIELDS[node["score"]]
        for index, node in enumerate(flow)
        if node.get("score") in SECTION_FIELDS
    }

    def column_for(key: str) -> str | None:
        for prefix, column in columns.items():
            if key.startswith(prefix):
                return column
        return None

    return await _drop(session, f"{room}:", column_for)


async def _drop(
    session: AsyncSession, prefix: str, column_for: Callable[[str], str | None]
) -> int:
    """Delete the events under one prefix and move the totals back by their value."""
    rows = list(
        (
            await session.execute(
                select(ScoreEvent).where(ScoreEvent.event_key.startswith(prefix))
            )
        ).scalars()
    )
    if not rows:
        return 0

    by_player: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        column = column_for(row.event_key)
        if column is None:  # pragma: no cover - guards a stale key
            continue
        by_player[row.player_id][column] += row.points

    for player_id, per_column in by_player.items():
        score = await session.get(Score, player_id)
        if score is None:  # pragma: no cover - an event implies a score row
            continue
        for column, points in per_column.items():
            setattr(score, column, (getattr(score, column) or 0) - points)

    await session.execute(
        delete(ScoreEvent).where(
            ScoreEvent.player_id.in_({row.player_id for row in rows}),
            ScoreEvent.event_key.in_({row.event_key for row in rows}),
        )
    )
    return len(rows)
