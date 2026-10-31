"""Player-reported scores.

The games run on the player's device with the question content, so the device is
the only place that can compute a score today. This endpoint is the narrow way
for it to hand that score back: a player token, its own player row, and a node
the flow actually marks as scoring.

Two properties matter more than the arithmetic:

* The section is decided by the flow node (`flow.py`'s `score` key), never by
  the client. A player cannot aim points at a column of their choosing.
* Reporting is at-least-once. Retries and replayed nodes are expected, so every
  report carries a server-built key and a repeat is silently ignored. The
  `score_events` row is written and the total moved in the same transaction, so
  a crash cannot leave one without the other.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_player
from app.database import get_session
from app.models import Player, Score, ScoreEvent
from app.players import SCORE_FIELDS, SECTION_FIELDS, score_body
from app.rooms import rooms

router = APIRouter(tags=["scores"])

# A single report is one answer (200 worst case) or one whole Data Shadow round
# (800). The bound only exists to reject a client bug or a hand-written request
# worth a whole leaderboard, so it sits well above the largest real report.
_POINTS_LIMIT = 2_000


class ScoreReport(BaseModel):
    room: str = Field(min_length=2, max_length=32)
    node_index: int = Field(ge=0)
    # None means "the whole node", which is how a single-submission game
    # (Data Shadow) reports; quizzes report per question.
    question: int | None = Field(default=None, ge=0)
    points: int = Field(ge=-_POINTS_LIMIT, le=_POINTS_LIMIT)


def _event_key(report: ScoreReport) -> str:
    question = "total" if report.question is None else str(report.question)
    return f"{report.room}:{report.node_index}:{question}"


@router.post("/me/score", summary="Report points earned on one answer")
async def report_score(
    body: ScoreReport,
    player: Player = Depends(require_player),
    session: AsyncSession = Depends(get_session),
) -> dict:
    engine = rooms.get(body.room)
    if engine is None:
        raise HTTPException(status_code=404, detail=f"unknown room: {body.room}")
    if body.node_index >= engine.node_count:
        raise HTTPException(status_code=422, detail=f"node {body.node_index} out of range")

    node = engine.flow[body.node_index]
    section = node.get("score")
    if section not in SECTION_FIELDS:
        raise HTTPException(status_code=422, detail=f"node {body.node_index} does not score")

    # A device that has not joined the room would otherwise be able to score
    # against a show it is not part of.
    if player.room_code != body.room:
        raise HTTPException(status_code=409, detail=f"not in room: {body.room}")

    column = SECTION_FIELDS[section]
    if column not in SCORE_FIELDS:  # pragma: no cover - guards a bad mapping
        raise HTTPException(status_code=500, detail="invalid score mapping")

    inserted = await session.execute(
        pg_insert(ScoreEvent)
        .values(player_id=player.id, event_key=_event_key(body), points=body.points)
        .on_conflict_do_nothing()
    )
    applied = inserted.rowcount == 1

    # Two questions answered within the same instant can both find no row, so
    # the row is created with an upsert rather than get-then-add.
    await session.execute(
        pg_insert(Score).values(player_id=player.id).on_conflict_do_nothing()
    )
    score = await session.get(Score, player.id)
    if score is None:  # pragma: no cover - the upsert above guarantees the row
        raise HTTPException(status_code=500, detail="score row missing")

    if applied and body.points:
        setattr(score, column, (getattr(score, column) or 0) + body.points)

    await session.commit()
    await session.refresh(score)
    return {"applied": applied, **score_body(player.id, score)}
