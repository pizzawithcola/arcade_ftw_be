from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.engine import game_state

router = APIRouter(tags=["ws"])


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "state", "payload": game_state.snapshot()})
    try:
        while True:
            message = await websocket.receive_json()
            await websocket.send_json({"type": "echo", "payload": message})
    except WebSocketDisconnect:
        pass
