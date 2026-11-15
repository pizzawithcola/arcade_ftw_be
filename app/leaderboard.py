"""The public leaderboard: everyone's total score, best first.

Only the nickname and the total are exposed - no player id, event, team or
room - because this hangs on a public screen behind the audience. The entry
is deliberately unauthenticated: anyone at the venue can open it, and it
carries nothing a stranger should not see.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.events import EVENT_KEYS
from app.models import Player, Score
from app.settings_store import read_active_event

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.get("", summary="Top players by total score")
async def leaderboard(
    limit: int = Query(default=10, ge=1, le=50),
    event: str | None = Query(
        default=None,
        description="Rank one event. `all` ignores the console's active event.",
    ),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The `limit` best totals, ranked for one event.

    Which event is the console's decision (`/api/settings.active_event`), not
    this endpoint's: the wall and the waiting room have to show the same board
    as the operator chose, and neither of them is a place to pick one. A query
    parameter overrides it for a one-off look, and `all` ranks every event
    together the way this endpoint used to.

    A player without a score row reads as zero, so the list stays complete
    while a room is still playing rather than hiding the not-yet-scored.
    """
    wanted = event.strip() if event else await read_active_event(session)
    scope = None if wanted in (None, "", "all") else wanted
    if scope is not None and scope not in EVENT_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown event: {scope}")

    query = (
        select(
            Player.nickname,
            func.coalesce(Score.total_score, 0).label("total_score"),
        )
        .outerjoin(Score, Score.player_id == Player.id)
        .order_by(
            func.coalesce(Score.total_score, 0).desc(),
            Player.id.asc(),
        )
        .limit(limit)
    )
    if scope is not None:
        query = query.where(Player.event == scope)

    rows = (await session.execute(query)).all()
    return {
        "event": scope,
        "players": [
            {"rank": index + 1, "nickname": nickname, "total_score": total}
            for index, (nickname, total) in enumerate(rows)
        ],
    }
