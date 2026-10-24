"""The game engine: advances the main flow on its own clock.

Two things make this more than a loop over a list:

* The countdown uses ``time.monotonic()``, so an NTP step or a host clock change
  cannot make the show jump. Clients are told the remaining time instead of
  being trusted to compute it from their own clock.
* Pausing *freezes the remaining time* rather than moving a wall-clock deadline,
  which is what makes pause/resume exact no matter how long the pause lasts.

The admin never drives the flow: it only intervenes (pause, resume, finish, jump
to a node). Everything else happens because time passed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

from app.flow import ROUND_FLOW

logger = logging.getLogger(__name__)


class Phase(str, Enum):
    WAITING = "waiting"
    PLAYING = "playing"
    PAUSED = "paused"
    FINISHED = "finished"


Listener = Callable[[], Awaitable[None]]


@dataclass
class Engine:
    flow: list[dict] = field(default_factory=lambda: [dict(node) for node in ROUND_FLOW])
    phase: Phase = Phase.WAITING
    index: int = 0

    # Monotonic instant the current node ends at. None while idle, paused or
    # finished, which is what makes "remaining_ms" meaningful only when playing.
    _deadline: float | None = None
    # Remaining seconds, kept while paused so resuming is exact.
    _remaining: float = 0.0
    _runner: asyncio.Task | None = None
    _wake: asyncio.Event | None = None
    _listeners: list[Listener] = field(default_factory=list)

    # ─── observers ────────────────────────────────────────────────────────────
    def subscribe(self, listener: Listener) -> None:
        """Called after every state change. Used to broadcast to all clients."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    async def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                await listener()
            except Exception:  # noqa: BLE001 - one bad client must not stall the show
                logger.exception("state listener failed")

    # ─── helpers ──────────────────────────────────────────────────────────────
    @property
    def node_count(self) -> int:
        return len(self.flow)

    def _node(self) -> dict | None:
        return self.flow[self.index] if 0 <= self.index < self.node_count else None

    def _arm(self) -> None:
        """Start the current node's countdown now, not on the runner's next tick.

        Arming synchronously matters: clients that read the state the instant
        /game/start returns would otherwise see a remaining time of zero.
        """
        self._deadline = time.monotonic() + self._duration()
        self._remaining = 0.0

    def _duration(self) -> float:
        node = self._node() or {}
        try:
            duration = float(node.get("duration_sec") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        # A node without a usable duration still gets a beat on screen.
        return duration if duration > 0 else 1.0

    def snapshot(self) -> dict:
        if self.phase is Phase.PLAYING and self._deadline is not None:
            remaining = max(0.0, self._deadline - time.monotonic())
        elif self.phase is Phase.PAUSED:
            remaining = max(0.0, self._remaining)
        else:
            remaining = 0.0

        return {
            "phase": self.phase.value,
            "index": self.index,
            "node_count": self.node_count,
            "node": self._node(),
            "remaining_ms": int(remaining * 1000),
            "question": self._question(remaining),
            "server_time_ms": int(time.time() * 1000),
        }

    def _question(self, remaining: float) -> dict | None:
        """Which question inside the current node is on screen.

        Derived from the node clock rather than reported by the devices: every
        screen then agrees with every other one, and a device that reconnects
        mid-question lands on the right question without having to be told.
        """
        node = self._node() or {}
        count = node.get("questions")
        seconds_each = node.get("seconds_each")
        if not count or not seconds_each:
            return None

        duration_ms = float(node.get("duration_sec") or 0) * 1000
        # Idle and finished have no clock running, so show the first question
        # rather than whichever one the maths would land on at zero remaining.
        if self.phase not in (Phase.PLAYING, Phase.PAUSED):
            return {"index": 0, "count": count, "seconds_each": seconds_each, "remaining_ms": 0}

        elapsed_ms = max(0.0, duration_ms - remaining * 1000)
        each_ms = float(seconds_each) * 1000
        index = min(count - 1, int(elapsed_ms // each_ms))
        return {
            "index": index,
            "count": count,
            "seconds_each": seconds_each,
            "remaining_ms": max(0, int(each_ms - (elapsed_ms - index * each_ms))),
        }

    # ─── lifecycle ────────────────────────────────────────────────────────────
    def _ensure_runner(self) -> None:
        if self._runner is None or self._runner.done():
            self._wake = asyncio.Event()
            self._runner = asyncio.create_task(self._run(), name="game-engine")

    async def _stop_runner(self) -> None:
        """Drop the clock task, so the engine only moves on command."""
        runner, self._runner = self._runner, None
        if runner is not None and not runner.done():
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass

    async def reset(self) -> None:
        """Back to a pristine show: first node, nothing on the clock.

        WAITING is unreachable once the show has started, which left no way to
        rehearse through to the end and then hand the console over ready to run
        from the top. Stopping the runner matters as much as the fields do: a
        live runner would sit on the wake event with nothing to wake it.
        """
        await self._stop_runner()
        self.index = 0
        self.phase = Phase.WAITING
        self._deadline = None
        self._remaining = 0.0
        await self._notify()

    async def start(self) -> None:
        if self.phase is Phase.PLAYING:
            return
        self.index = 0
        self.phase = Phase.PLAYING
        self._arm()
        self._ensure_runner()
        assert self._wake is not None
        self._wake.set()
        await self._notify()

    async def pause(self) -> None:
        if self.phase is not Phase.PLAYING:
            return
        if self._deadline is not None:
            self._remaining = max(0.0, self._deadline - time.monotonic())
            self._deadline = None
        self.phase = Phase.PAUSED
        assert self._wake is not None
        self._wake.set()
        await self._notify()

    async def resume(self) -> None:
        if self.phase is not Phase.PAUSED:
            return
        self.phase = Phase.PLAYING
        self._deadline = time.monotonic() + (self._remaining or self._duration())
        assert self._wake is not None
        self._wake.set()
        await self._notify()

    async def finish(self) -> None:
        self.phase = Phase.FINISHED
        self._deadline = None
        self._remaining = 0.0
        if self._wake is not None:
            self._wake.set()
        await self._notify()

    async def go_to(self, index: int) -> None:
        """Jump to any node. Also the way out of FINISHED and WAITING."""
        if not 0 <= index < self.node_count:
            raise ValueError(f"node index {index} out of range")
        was_waiting = self.phase in (Phase.WAITING, Phase.FINISHED)
        self.index = index
        self._deadline = None
        if was_waiting:
            self.phase = Phase.PLAYING
            self._ensure_runner()
        if self.phase is Phase.PLAYING:
            self._arm()
        else:
            # Paused: hold the new node's full length rather than zero, so a
            # jump does not park the screen on 0:00 for a node it has not run.
            self._remaining = self._duration()
        assert self._wake is not None
        self._wake.set()
        await self._notify()

    # ─── the clock ────────────────────────────────────────────────────────────
    async def _run(self) -> None:
        try:
            while True:
                if self.phase is Phase.FINISHED:
                    return
                if self.phase is not Phase.PLAYING:
                    assert self._wake is not None
                    await self._wake.wait()
                    self._wake.clear()
                    continue

                if self._deadline is None:
                    self._deadline = time.monotonic() + self._duration()

                timeout = max(0.0, self._deadline - time.monotonic())
                try:
                    assert self._wake is not None
                    await asyncio.wait_for(self._wake.wait(), timeout=timeout)
                    self._wake.clear()
                    continue  # a control action changed the state; re-evaluate
                except TimeoutError:
                    pass

                self._deadline = None
                if self.index + 1 >= self.node_count:
                    self.phase = Phase.FINISHED
                    await self._notify()
                    return
                self.index += 1
                # Arm before announcing: clients count down from this snapshot,
                # so a new node must never be broadcast with zero time left.
                self._arm()
                await self._notify()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let the show die silently
            logger.exception("game engine crashed")
            self.phase = Phase.FINISHED
            await self._notify()


engine = Engine()
