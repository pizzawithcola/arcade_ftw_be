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
from app.models import Player, Room
from app.rooms import rooms

router = APIRouter(prefix="/rooms", tags=["rooms"])


def _engine(code: str) -> Engine:
    engine = rooms.get(code)
    if engine is None:
        raise HTTPException(status_code=404, detail=f"unknown room: {code}")
    return engine


@router.get("", summary="Every room with its state, players and client count")
async def list_rooms(
    _: Claims = Depends(require_display),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Feeds the admin panel and the display's room picker.

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
    # label and event come from the row, not the engine: they are metadata the
    # operator table shows next to the live state.
    meta = {
        row.code: {"event": row.event, "label": row.label}
        for row in (await session.execute(select(Room.code, Room.event, Room.label))).all()
    }
    return {
        "rooms": [
            {
                **snapshot,
                "players": counts.get(snapshot["code"], 0),
                **meta.get(snapshot["code"], {"event": None, "label": None}),
            }
            for snapshot in rooms.snapshots()
        ]
    }


# Lowercase, no leading dash, at most 32: it lands in URLs, paths and the join QR,
# and a code with a slash or a capital would make `/r/{code}` ambiguous.
_ROOM_CODE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")


class NewRoom(BaseModel):
    code: str
    event: str | None = None
    label: str | None = Field(default=None, max_length=80)


class RoomPatch(BaseModel):
    event: str | None = None
    label: str | None = Field(default=None, max_length=80)


def _check_event(event: str | None) -> None:
    if event is not None and event not in EVENT_KEYS:
        raise HTTPException(status_code=422, detail=f"unknown event: {event}")


@router.post("", status_code=201, summary="Create a room")
async def create_room(
    body: NewRoom,
    _: Claims = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """A new room is a row plus an engine: the show is playable the moment it exists."""
    if not _ROOM_CODE.match(body.code):
        raise HTTPException(
            status_code=422,
            detail="code must be lowercase letters, digits and dashes (2-32 chars)",
        )
    _check_event(body.event)
    if await session.get(Room, body.code) is not None:
        raise HTTPException(status_code=409, detail=f"room already exists: {body.code}")

    room = Room(code=body.code, event=body.event, label=body.label)
    session.add(room)
    await session.commit()
    await session.refresh(room)
    # The engine is built before answering, so a client that immediately opens
    # the room URL does not get a 404 for a room that was just created.
    rooms.ensure(body.code)
    return _room_body(room, rooms.get(body.code))


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
    return engine.snapshot()


@router.post(
    "/{code}/resume", summary="Unfreeze the clock", dependencies=[Depends(require_admin)]
)
async def resume(code: str) -> dict:
    engine = _engine(code)
    await engine.resume()
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
