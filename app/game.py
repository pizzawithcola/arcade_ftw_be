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

from fastapi import APIRouter, Depends, HTTPException

from app.auth import Claims, require_admin, require_display
from app.engine import Engine
from app.rooms import rooms

router = APIRouter(prefix="/rooms", tags=["rooms"])


def _engine(code: str) -> Engine:
    engine = rooms.get(code)
    if engine is None:
        raise HTTPException(status_code=404, detail=f"unknown room: {code}")
    return engine


@router.get("", summary="Every room with its state and client count")
async def list_rooms(_: Claims = Depends(require_display)) -> dict:
    """Feeds the admin panel and the display's room picker."""
    return {"rooms": rooms.snapshots()}


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
