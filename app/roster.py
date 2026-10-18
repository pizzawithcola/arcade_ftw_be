"""Who is in a room, and how they are doing.

Membership is a row on `players` rather than a table of its own: a player is in
at most one room at a time, so a join table would only ever hold one row per
person.

Scores are read with a LEFT JOIN because a player exists from the moment they
log in, while a score row only appears once they finish a game. Everyone in the
room has to be on the roster from the start, so a missing score reads as zero.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Claims, require_display, require_player
from app.database import get_session
from app.models import Player, Room, Score
from app.rooms import rooms

router = APIRouter(prefix="/rooms", tags=["rooms"])


def _known_room(code: str) -> None:
    """Refuse a room that has no engine yet.

    Without this a typo would create a join row pointing at nothing, and the
    player would wait forever on a screen nobody is driving.
    """
    if rooms.get(code) is None:
        raise HTTPException(status_code=404, detail=f"unknown room: {code}")


@router.post("/{code}/join", summary="Put the signed-in player into a room")
async def join(
    code: str,
    player: Player = Depends(require_player),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Record which room this player is playing in.

    Called again on every page load, so a reconnecting device lands back in the
    room it was in rather than being told it never joined.
    """
    _known_room(code)
    if await session.get(Room, code) is None:
        raise HTTPException(status_code=404, detail=f"unknown room: {code}")

    player.room_code = code
    await session.commit()
    return {
        "room": code,
        "player_id": player.id,
        "nickname": player.nickname,
        "event": player.event,
    }


@router.get("/{code}/players", summary="The room roster with each player's total")
async def players(
    code: str,
    _: Claims = Depends(require_display),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Feeds the waiting room and the admin panel, best score first."""
    _known_room(code)
    rows = await session.execute(
        select(
            Player.id,
            Player.nickname,
            Player.event,
            func.coalesce(Score.total_score, 0).label("total_score"),
        )
        .outerjoin(Score, Score.player_id == Player.id)
        .where(Player.room_code == code)
        .order_by(func.coalesce(Score.total_score, 0).desc(), Player.id)
    )
    return {
        "room": code,
        "players": [
            {
                "player_id": row.id,
                "nickname": row.nickname,
                "event": row.event,
                "total_score": row.total_score,
            }
            for row in rows
        ],
    }
