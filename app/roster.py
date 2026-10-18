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
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    ADMIN_SUBJECT,
    Claims,
    free_nickname,
    require_admin,
    require_player,
    require_signed_in,
)
from app.database import get_session
from app.events import EVENT_KEYS
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


class NewPlayer(BaseModel):
    nickname: str = Field(min_length=1, max_length=64)
    event: str


@router.post(
    "/{code}/players",
    summary="Add a player to a room without them signing in",
    status_code=201,
)
async def add_player(
    code: str,
    body: NewPlayer,
    _: Claims = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Operator-created membership.

    Same as a login plus a join, done on the player's behalf - useful for a
    dropped iPad mid-show, where the person is in the room but their device is
    not. A taken nickname is numbered exactly like a login, so this can never
    merge two people.
    """
    room = await session.get(Room, code)
    if room is None:
        raise HTTPException(status_code=404, detail=f"unknown room: {code}")
    nickname = body.nickname.strip()
    if not nickname:
        raise HTTPException(status_code=422, detail="nickname required")
    if body.event not in EVENT_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown event: {body.event}")

    player = Player(
        nickname=await free_nickname(nickname, body.event, session),
        event=body.event,
        room_code=code,
    )
    session.add(player)
    await session.commit()
    await session.refresh(player)
    return {
        "player_id": player.id,
        "nickname": player.nickname,
        "event": player.event,
        "room": player.room_code,
    }


@router.get("/{code}/players", summary="The room roster")
async def players(
    code: str,
    claims: Claims = Depends(require_signed_in),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Feeds the waiting room and the admin panel, best score first.

    Players see only the names, because the waiting room is a list of who is
    here. Scores and event are for the operator and the big screen; the same
    endpoint would otherwise be a leaderboard every iPad can poll.
    """
    _known_room(code)
    rows = (
        await session.execute(
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
    ).all()

    if claims.subject != ADMIN_SUBJECT:
        return {
            "room": code,
            "count": len(rows),
            "players": [{"nickname": row.nickname} for row in rows],
        }
    return {
        "room": code,
        "count": len(rows),
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
