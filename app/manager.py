"""Keeps every open websocket and fans one message out to all of them."""

from __future__ import annotations

import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    @property
    def count(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        logger.info("client connected; %d online", self.count)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        logger.info("client left; %d online", self.count)

    async def broadcast(self, message: dict) -> None:
        """Send to everyone, dropping any connection that has gone away.

        Iterates a snapshot of the set because disconnecting mutates it.
        """
        dead: list[WebSocket] = []
        for websocket in list(self._connections):
            try:
                await websocket.send_json(message)
            except Exception:  # noqa: BLE001 - a closed socket is expected, not a bug
                dead.append(websocket)
        for websocket in dead:
            self.disconnect(websocket)


manager = ConnectionManager()
