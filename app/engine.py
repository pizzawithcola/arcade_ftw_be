from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.flow import ROUND_FLOW


class Phase(str, Enum):
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    FINISHED = "finished"


@dataclass
class GameState:
    phase: Phase = Phase.IDLE
    current_index: int = 0
    flow: list[dict] = field(default_factory=lambda: list(ROUND_FLOW))

    @property
    def current(self) -> dict | None:
        if 0 <= self.current_index < len(self.flow):
            return self.flow[self.current_index]
        return None

    def start(self) -> None:
        self.phase = Phase.PLAYING
        self.current_index = 0

    def pause(self) -> None:
        if self.phase is Phase.PLAYING:
            self.phase = Phase.PAUSED

    def resume(self) -> None:
        if self.phase is Phase.PAUSED:
            self.phase = Phase.PLAYING

    def finish(self) -> None:
        self.phase = Phase.FINISHED

    def go_to(self, index: int) -> None:
        if 0 <= index < len(self.flow):
            self.current_index = index

    def next(self) -> None:
        self.go_to(self.current_index + 1)

    def snapshot(self) -> dict:
        return {
            "phase": self.phase.value,
            "current_index": self.current_index,
            "current": self.current,
        }


game_state = GameState()
