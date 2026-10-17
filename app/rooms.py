"""Rooms: one independent show per room code.

Even when only one show runs at a time, room is part of the API shape from the
start. Otherwise every fetch, the websocket protocol and the front-end routes
would have to change the day a second event runs in parallel - and that day is
always the day of the event.

Engines stay in memory, one per room. A restart therefore resets every show to
IDLE; that is the same constraint the single-room version already had.
"""

from __future__ import annotations

import logging

from app.engine import Engine
from app.manager import manager

logger = logging.getLogger(__name__)

DEFAULT_ROOM = "main"


class RoomRegistry:
    def __init__(self) -> None:
        self._engines: dict[str, Engine] = {}

    def _build(self, code: str) -> Engine:
        engine = Engine()

        async def notify() -> None:
            # Bound per room, so a change in one show never reaches another.
            await manager.broadcast_to(code, {"type": "state", "payload": engine.snapshot()})

        engine.subscribe(notify)
        logger.info("room %s ready", code)
        return engine

    def ensure_ready(self) -> None:
        """Create the default room. Called on startup, not at import."""
        self.ensure(DEFAULT_ROOM)

    def ensure(self, code: str) -> Engine:
        engine = self._engines.get(code)
        if engine is None:
            engine = self._build(code)
            self._engines[code] = engine
        return engine

    def get(self, code: str) -> Engine | None:
        return self._engines.get(code)

    def codes(self) -> list[str]:
        return sorted(self._engines)

    def snapshots(self) -> list[dict]:
        return [
            {
                "code": code,
                "state": self._engines[code].snapshot(),
                "clients": manager.count(code),
            }
            for code in self.codes()
        ]


rooms = RoomRegistry()
