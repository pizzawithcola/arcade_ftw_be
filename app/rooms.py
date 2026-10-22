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

from sqlalchemy import select

from app.database import SessionLocal
from app.engine import Engine
from app.manager import manager
from app.models import Room
from app.sabotage import auto_start_sabotage

logger = logging.getLogger(__name__)

DEFAULT_ROOM = "main"


class RoomRegistry:
    def __init__(self) -> None:
        self._engines: dict[str, Engine] = {}

    def _build(self, code: str) -> Engine:
        engine = Engine()

        async def notify() -> None:
            # The main flow reaching the sabotage node starts that game on its
            # own, so the show needs no operator input at that point.
            await auto_start_sabotage(code, engine)
            # Bound per room, so a change in one show never reaches another.
            await manager.broadcast_to(code, {"type": "state", "payload": engine.snapshot()})

        engine.subscribe(notify)
        logger.info("room %s ready", code)
        return engine

    async def load(self) -> None:
        """Create an engine for every room the database knows about.

        Rooms are data now, so which ones exist on boot is a query rather than a
        hardcoded default. A database failure must not stop the process from
        starting: the show is in memory and the ALB would otherwise kill the
        task mid-event, wiping every room's state. Falling back to the default
        room keeps a show playable without the database.
        """
        try:
            async with SessionLocal() as session:
                result = await session.execute(select(Room.code).order_by(Room.code))
                codes = result.scalars().all()
        except Exception as exc:  # noqa: BLE001 - any failure means we cannot read rooms
            logger.warning("could not load rooms from the database: %s", exc)
            codes = [DEFAULT_ROOM]
        for code in codes:
            self.ensure(code)

    def ensure(self, code: str) -> Engine:
        engine = self._engines.get(code)
        if engine is None:
            engine = self._build(code)
            self._engines[code] = engine
        return engine

    def get(self, code: str) -> Engine | None:
        return self._engines.get(code)

    def remove(self, code: str) -> None:
        """Forget a room. Clients still connected to it simply stop hearing news."""
        engine = self._engines.pop(code, None)
        if engine is not None:
            logger.info("room %s removed", code)

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
