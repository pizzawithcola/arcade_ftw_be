"""Keeps every open websocket, grouped by room, and fans messages out per room.

Grouping matters: two shows can run side by side, and a state change in one must
never reach the screens watching the other.
"""

from __future__ import annotations

import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}
        # Which room a socket belongs to, so disconnect does not have to be told.
        self._where: dict[WebSocket, str] = {}

    def count(self, room: str | None = None) -> int:
        if room is None:
            return len(self._where)
        return len(self._rooms.get(room, ()))

    async def connect(self, websocket: WebSocket, room: str) -> None:
        await websocket.accept()
        self._rooms.setdefault(room, set()).add(websocket)
        self._where[websocket] = room
        logger.info(
            "client connected to %s; %d in room, %d online", room, self.count(room), self.count()
        )

    def disconnect(self, websocket: WebSocket) -> None:
        room = self._where.pop(websocket, None)
        if room is not None:
            self._rooms.get(room, set()).discard(websocket)
        logger.info("client left; %d online", self.count())

    async def broadcast_to(self, room: str, message: dict) -> None:
        """Send to one room, dropping any connection that has gone away."""
        sockets = list(self._rooms.get(room, ()))
        dead: list[WebSocket] = []
        for websocket in sockets:
            try:
                await websocket.send_json(message)
            except Exception:  # noqa: BLE001 - a closed socket is expected, not a bug
                dead.append(websocket)
        for websocket in dead:
            self.disconnect(websocket)


manager = ConnectionManager()
