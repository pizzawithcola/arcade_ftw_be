"""The websocket every screen listens on.

Clients are listeners only. On connect they get the room's current state straight
away so a screen that comes up mid-show is instantly in sync, and after that they
receive whatever that room's engine broadcasts.

The room comes in as a query parameter (`/api/ws?room=main`) because browsers
cannot set a header on a WebSocket handshake and a token would leak into URLs.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.manager import manager
from app.rooms import DEFAULT_ROOM, rooms

router = APIRouter(tags=["ws"])

# 1008 = policy violation, which is what an unknown room is.
UNKNOWN_ROOM = 1008


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    code = websocket.query_params.get("room") or DEFAULT_ROOM
    engine = rooms.get(code)
    if engine is None:
        await websocket.close(code=UNKNOWN_ROOM, reason=f"unknown room: {code}")
        return

    await manager.connect(websocket, code)
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
