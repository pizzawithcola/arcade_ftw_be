"""The public leaderboard: everyone's total score, best first.

Only the nickname and the total are exposed - no player id, event, team or
room - because this hangs on a public screen behind the audience. The entry
is deliberately unauthenticated: anyone at the venue can open it, and it
carries nothing a stranger should not see.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Player, Score

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.get("", summary="Top players by total score")
async def leaderboard(
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """The `limit` best totals across every room and event.

    A player without a score row reads as zero, so the list stays complete
    while a room is still playing rather than hiding the not-yet-scored.
    """
    rows = (
        await session.execute(
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
    ).all()
    return {
        "players": [
            {"rank": index + 1, "nickname": nickname, "total_score": total}
            for index, (nickname, total) in enumerate(rows)
        ]
    }
