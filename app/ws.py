"""The websocket every screen listens on.

Clients are listeners only. On connect they get the room's current state straight
away so a screen that comes up mid-show is instantly in sync, and after that they
receive whatever that room's engine broadcasts.

The room comes in as a query parameter (`/api/ws?room=main`) because browsers
cannot set a header on a WebSocket handshake and a token would leak into URLs.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.database import SessionLocal
from app.manager import manager
from app.rooms import DEFAULT_ROOM, rooms
from app.settings_store import read_theme
from app.sabotage import sabotages

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
        # The theme first: a screen that comes up in a light room should not paint
        # one dark frame while it waits for the state.
        async with SessionLocal() as session:
            theme = await read_theme(session)
        await websocket.send_json({"type": "theme", "payload": {"theme": theme}})
        await websocket.send_json({"type": "state", "payload": engine.snapshot()})
        # A room mid-Sabotage should land a reconnecting screen straight into
        # the game, not wait for the next phase change to hear about it.
        sabotage = sabotages.get(code)
        if sabotage is not None:
            await websocket.send_json({"type": "sabotage", "payload": sabotage.snapshot()})
        while True:
            # Nothing a client sends changes the show, but receiving is what
            # keeps this loop parked until the connection actually drops.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)
