"""The websocket every screen listens on.

Clients are listeners only. On connect they get the current state straight away
so a screen that comes up mid-show is instantly in sync, and after that they
receive whatever the engine broadcasts.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.engine import engine
from app.manager import manager

router = APIRouter(tags=["ws"])


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        await websocket.send_json({"type": "state", "payload": engine.snapshot()})
        while True:
            # Nothing a client sends changes the show, but receiving is what
            # keeps this loop parked until the connection actually drops.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)
