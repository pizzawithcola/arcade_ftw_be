"""Public game state and the admin controls that intervene with it.

Controls are HTTP rather than websocket messages on purpose: they are
authenticated, they show up in /docs where they can be clicked, and a failure
comes back with a real status code. The resulting state change is broadcast to
every connected client from the engine's listener.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_admin
from app.engine import engine

router = APIRouter(prefix="/game", tags=["game"])


@router.get("/state", summary="Current phase and node")
async def state() -> dict:
    return engine.snapshot()


@router.post("/start", summary="Start the show from the first node", dependencies=[Depends(require_admin)])
async def start() -> dict:
    await engine.start()
    return engine.snapshot()


@router.post("/pause", summary="Freeze the clock", dependencies=[Depends(require_admin)])
async def pause() -> dict:
    await engine.pause()
    return engine.snapshot()


@router.post("/resume", summary="Unfreeze the clock", dependencies=[Depends(require_admin)])
async def resume() -> dict:
    await engine.resume()
    return engine.snapshot()


@router.post("/finish", summary="End the show", dependencies=[Depends(require_admin)])
async def finish() -> dict:
    await engine.finish()
    return engine.snapshot()


@router.post("/go/{index}", summary="Jump to a node", dependencies=[Depends(require_admin)])
async def go_to(index: int) -> dict:
    try:
        await engine.go_to(index)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return engine.snapshot()
