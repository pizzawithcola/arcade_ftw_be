"""Public room state and the admin controls that intervene with it.

Controls are HTTP rather than websocket messages on purpose: they are
authenticated, they show up in /docs where they can be clicked, and a failure
comes back with a real status code. The resulting state change is broadcast to
every client watching that room from the engine's listener.

The room is in the path from the very first version. Even with a single show,
`/rooms/main/...` means adding a second room later is data, not a protocol
change.
"""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import Claims, require_admin, require_display
from app.database import get_session
from app.engine import Engine, Phase
from app.events import EVENT_KEYS
from app.manager import manager
from app.models import Player, Room
from app.rooms import rooms
from app.sabotage import sabotages

router = APIRouter(prefix="/rooms", tags=["rooms"])


def _engine(code: str) -> Engine:
    engine = rooms.get(code)
    if engine is None:
        raise HTTPException(status_code=404, detail=f"unknown room: {code}")
    return engine


@router.get("", summary="Every room, newest first, and which one is live")
async def list_rooms(
    _: Claims = Depends(require_display),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Feeds the admin panel and the big screen.

    A room is one round: the operator creates a new one per round and the screens
    follow it. So the list is newest first and `current` names the room the big
    screen should be showing - the one with the latest `created_at`.

    `players` counts people who joined the room in the database; `clients` counts
    live websocket connections. They differ whenever a device has joined but not
    connected, or connected with a token from another room.
    """
    counts = dict(
        (
            await session.execute(
                select(Player.room_code, func.count())
                .where(Player.room_code.is_not(None))
                .group_by(Player.room_code)
            )
        ).all()
    )
    rows = (
        await session.execute(select(Room).order_by(Room.created_at.desc(), Room.code.desc()))
    ).scalars()
    live = {snapshot["code"]: snapshot for snapshot in rooms.snapshots()}

    ordered = [
        {
            **live[row.code],
            "players": counts.get(row.code, 0),
            "event": row.event,
            "label": row.label,
            "created_at": row.created_at,
        }
        # A row without an engine cannot be shown or driven; loading rooms at
        # startup is what normally keeps the two in step.
        for row in rows
        if row.code in live
    ]
    return {"current": ordered[0]["code"] if ordered else None, "rooms": ordered}


# Lowercase, no leading dash, at most 32: it lands in URLs, paths and the join QR,
# and a code with a slash or a capital would make `/r/{code}` ambiguous.
_ROOM_CODE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


class NewRoom(BaseModel):
    # Blank means "give me the next round-N".
    code: str | None = None
    event: str | None = None
    label: str | None = Field(default=None, max_length=80)


class RoomPatch(BaseModel):
    event: str | None = None
    label: str | None = Field(default=None, max_length=80)


def _check_event(event: str | None) -> None:
    if event is not None and event not in EVENT_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown event: {event}")


@router.post("", status_code=201, summary="Create a room for the next round")
async def create_room(
    body: NewRoom,
    _: Claims = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """A new room is a row plus an engine: the show is playable the moment it exists.

    Without a code the next `round-N` is used - starting a round should be one
    click, not something to invent while thirty people watch.
    """
    codes = list((await session.execute(select(Room.code))).scalars().all())
    code = (body.code or "").strip() or _next_round_code(codes)
    if not _ROOM_CODE.match(code):
        raise HTTPException(
            status_code=422,
            detail="code must be lowercase letters, digits and dashes (2-32 chars)",
        )
    _check_event(body.event)
    if code in codes:
        raise HTTPException(status_code=409, detail=f"room already exists: {code}")

    room = Room(code=code, event=body.event, label=body.label)
    session.add(room)
    await session.commit()
    await session.refresh(room)
    # The engine is built before answering, so a client that immediately opens
    # the room URL does not get a 404 for a room that was just created.
    rooms.ensure(code)
    return _room_body(room, rooms.get(code))


def _next_round_code(codes: list[str]) -> str:
    """`round-1`, `round-2`, … past whatever already exists."""
    highest = 0
    for existing in codes:
        match = re.fullmatch(r"round-(\d+)", existing)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"round-{highest + 1}"


@router.get("/current", summary="The room players join")
async def current_room(session: AsyncSession = Depends(get_session)) -> dict:
    """Public on purpose: a player's device has to find the room before it has
    any other right to ask. Only the code and the phase are exposed.

    Players do not scan: their iPad lands on the login page between rounds and
    whatever round is newest is the one they join.
    """
    room = (
        await session.execute(
            select(Room).order_by(Room.created_at.desc(), Room.code.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="no room yet")
    engine = rooms.get(room.code)
    return {
        "code": room.code,
        "phase": engine.snapshot()["phase"] if engine is not None else "idle",
    }


@router.get("/{code}", summary="One room: metadata, headcount and state")
async def room_detail(
    code: str,
    _: Claims = Depends(require_display),
    session: AsyncSession = Depends(get_session),
) -> dict:
    room = await session.get(Room, code)
    if room is None:
        raise HTTPException(status_code=404, detail=f"unknown room: {code}")
    return _room_body(room, _engine(code))


@router.patch("/{code}", summary="Rename a room or change its event")
async def patch_room(
    code: str,
    body: RoomPatch,
    _: Claims = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Changing the code is refused rather than supported: the code is in URLs,
    QR codes already printed and players' stored membership."""
    room = await session.get(Room, code)
    if room is None:
        raise HTTPException(status_code=404, detail=f"unknown room: {code}")
    _check_event(body.event)
    if body.event is not None:
        room.event = body.event
    if body.label is not None:
        room.label = body.label
    await session.commit()
    await session.refresh(room)
    return _room_body(room, _engine(code))


@router.delete("/{code}", status_code=204, summary="Delete a room")
async def delete_room(
    code: str,
    _: Claims = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Refused while a show is running, because deleting the engine would strand
    every iPad in the room mid-round. Players stay in the database with no room."""
    engine = _engine(code)
    if engine.snapshot()["phase"] in (Phase.PLAYING, Phase.PAUSED):
        raise HTTPException(
            status_code=409,
            detail="room is playing; finish or reset it before deleting",
        )
    # A statement rather than session.delete(): the row's members have to be
    # released by the database (ON DELETE SET NULL), not loaded and rewritten.
    await session.execute(delete(Room).where(Room.code == code))
    await session.commit()
    rooms.remove(code)
    sabotages.remove(code)


def _room_body(room: Room, engine: Engine | None) -> dict:
    created = room.created_at
    return {
        "code": room.code,
        "event": room.event,
        "label": room.label,
        "created_at": created.isoformat() if isinstance(created, datetime) else created,
        "state": engine.snapshot() if engine is not None else None,
    }


@router.get("/{code}/state", summary="Current phase and node")
async def state(code: str) -> dict:
    return _engine(code).snapshot()


@router.post(
    "/{code}/start",
    summary="Start the show from the first node",
    dependencies=[Depends(require_admin)],
)
async def start(code: str) -> dict:
    engine = _engine(code)
    await engine.start()
    return engine.snapshot()


@router.post(
    "/{code}/pause", summary="Freeze the clock", dependencies=[Depends(require_admin)]
)
async def pause(code: str) -> dict:
    engine = _engine(code)
    await engine.pause()
    # One operator action stops every clock in the room, including Sabotage's
    # per-phase timer. Broadcast the frozen sabotage state too, so screens show
    # it the same instant the main clock freezes.
    sabotage = sabotages.get(code)
    if sabotage is not None:
        sabotage.pause()
        await manager.broadcast_to(code, {"type": "sabotage", "payload": sabotage.snapshot()})
    return engine.snapshot()


@router.post(
    "/{code}/resume", summary="Unfreeze the clock", dependencies=[Depends(require_admin)]
)
async def resume(code: str) -> dict:
    engine = _engine(code)
    await engine.resume()
    sabotage = sabotages.get(code)
    if sabotage is not None:
        sabotage.resume()
        await manager.broadcast_to(code, {"type": "sabotage", "payload": sabotage.snapshot()})
    return engine.snapshot()


@router.post("/{code}/finish", summary="End the show", dependencies=[Depends(require_admin)])
async def finish(code: str) -> dict:
    engine = _engine(code)
    await engine.finish()
    return engine.snapshot()


@router.post(
    "/{code}/reset",
    summary="Rewind to an unstarted show at the first node",
    dependencies=[Depends(require_admin)],
)
async def reset(code: str) -> dict:
    engine = _engine(code)
    await engine.reset()
    return engine.snapshot()


@router.post("/{code}/go/{index}", summary="Jump to a node", dependencies=[Depends(require_admin)])
async def go_to(code: str, index: int) -> dict:
    engine = _engine(code)
    try:
        await engine.go_to(index)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return engine.snapshot()
