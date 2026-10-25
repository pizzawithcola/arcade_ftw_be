"""The Sabotage engine: one per room, in memory like the show engine.

A room's players are already split into Red and Blue (see the teams endpoint),
so this engine only runs the game that split feeds: three attack/defence
rounds where each round's action is the card a majority voted for.

The single-use Attack deck is shared: every engine lives in process memory and
`spent` is global to the room, never per team. The Defence deck reappears each
round. The whole show is driven by a monotonic clock like the main engine, so
pausing the main flow is enough to pause this one too - both read the same
wall-clock only for display.

Votes are a plain map from player to card. A vote cast after the timer has
already advanced is refused by phase, so the last accepted vote before the
deadline is the one that counts.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app import sabotage_config as cfg

logger = logging.getLogger(__name__)

TEAMS = ("red", "blue")


class SabotageError(Exception):
    """A player or host action that the current phase cannot accept."""


@dataclass
class SabotageEngine:
    code: str

    phase: str = "setup"  # setup / team_reveal / attack / defend / tie_break /
    # resolve / team_result / payout / done
    round: int = 1

    teams: dict[str, set[int]] = field(default_factory=lambda: {"red": set(), "blue": set()})
    budgets: dict[str, int] = field(default_factory=lambda: {"red": 0, "blue": 0})
    scores: dict[str, int] = field(default_factory=lambda: {"red": 0, "blue": 0})
    spent: set[str] = field(default_factory=set)
    votes: dict[int, str] = field(default_factory=dict)

    # The card the acting team settled on, and the defence played against it.
    attack_card: str | None = None
    defence_card: str | None = None
    last_result: dict | None = None
    # When a vote ties, only these cards may be voted on again.
    tie_candidates: list[str] = field(default_factory=list)
    # The phase a tie returns to once broken.
    _after_tie: str | None = None

    # ─── clock, same discipline as the main engine ───────────────────────────
    _deadline: float | None = None
    _runner: asyncio.Task | None = None
    _wake: asyncio.Event | None = None
    _listeners: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    # Pause freezes the current phase's remaining time, exactly like the main
    # engine, so one operator action stops both clocks together.
    paused: bool = False
    _remaining: float = 0.0
    # Whether the room broadcast is already wired, so a re-start does not add
    # duplicate listeners.
    _wired: bool = False
    # Called once the game reaches `done`, to persist team scores.
    _on_finish: Callable[[], Awaitable[None]] | None = None

    # ─── observers ───────────────────────────────────────────────────────────
    def subscribe(self, listener: Callable[[], Awaitable[None]]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    async def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                await listener()
            except Exception:  # noqa: BLE001 - one bad listener must not stall the show
                logger.exception("sabotage listener failed")

    # ─── helpers ─────────────────────────────────────────────────────────────
    def _scenario(self) -> dict:
        return cfg.SCENARIOS[min(self.round, len(cfg.SCENARIOS)) - 1]

    def _attacker(self) -> str:
        return self._scenario()["attacker"]

    def _defender(self) -> str:
        return self._scenario()["defender"]

    def _voting_team(self) -> str:
        """Who is voting right now, or None outside a vote phase."""
        if self.phase == "attack":
            return self._attacker()
        if self.phase == "defend":
            return self._defender()
        if self.phase == "tie_break":
            # A tie revotes the side whose vote just tied.
            return self._attacker() if self._after_tie == "attack" else self._defender()
        return ""

    def _attack_options(self) -> list[str]:
        return [
            card
            for card, spec in cfg.ATTACK_CARDS.items()
            if card not in self.spent and spec["cost"] <= self.budgets[self._attacker()]
        ]

    def _defence_options(self) -> list[str]:
        return [
            card
            for card, spec in cfg.DEFENCE_CARDS.items()
            if spec["cost"] <= self.budgets[self._defender()]
        ]

    def _arm(self) -> None:
        duration = cfg.TIMINGS.get(self.phase, 5)
        self._deadline = time.monotonic() + duration

    def _vote_outcome(self, candidates: list[str]) -> tuple[str | None, list[str] | None]:
        """(winner, tied) - the unique most-voted card, or the tied set.

        No vote at all counts as every candidate tied, so the revote offers the
        full deck rather than picking for people who never looked down.
        """
        tally = {card: 0 for card in candidates}
        for card in self.votes.values():
            if card in tally:
                tally[card] += 1
        if not tally:
            return None, list(candidates)
        top = max(tally.values())
        winners = [card for card, count in tally.items() if count == top]
        return (winners[0], None) if len(winners) == 1 else (None, winners)

    def _cheapest(self, candidates: list[str], deck: dict) -> str:
        return min(candidates, key=lambda card: deck[card]["cost"])

    def _start_tie(self, candidates: list[str], after: str) -> None:
        self.tie_candidates = list(candidates)
        self._after_tie = after
        self.votes = {}
        self.phase = "tie_break"
        self._arm()

    # ─── the show ────────────────────────────────────────────────────────────
    def start(self) -> None:
        """Begin the reveal. Teams must already be assigned and non-empty."""
        if self.phase != "setup":
            raise SabotageError("already started")
        for team in TEAMS:
            if not self.teams[team]:
                raise SabotageError(f"team {team} is empty; assign teams first")
        self.budgets = {team: cfg.STARTING_BUDGET for team in TEAMS}
        self.scores = {team: 0 for team in TEAMS}
        self.spent = set()
        self.round = 1
        self.phase = "team_reveal"
        self._arm()

    def cast_vote(self, player_id: int, card_id: str) -> str:
        """Record one player's current choice. The last valid one wins."""
        if self.paused:
            raise SabotageError("show is paused")
        team = self._voting_team()
        if not team:
            raise SabotageError(f"no vote is open in phase {self.phase}")
        if player_id not in self.teams[team]:
            raise SabotageError("only the acting team votes")

        if self.phase == "attack":
            if card_id not in self._attack_options():
                raise SabotageError("card is not available to the attacking team")
        elif self.phase == "defend":
            if card_id not in self._defence_options():
                raise SabotageError("card is not available to the defending team")
        else:  # tie_break
            if card_id not in self.tie_candidates:
                raise SabotageError("card is not among the tied cards")

        self.votes[player_id] = card_id
        return card_id

    def advance(self) -> None:
        """Step the state machine once, whatever the phase demands.

        Pure and synchronous on purpose: the async runner calls it on the
        clock, and tests call it directly to move a whole show instantly.
        """
        if self.phase == "setup":
            return

        if self.phase == "team_reveal":
            self._begin_attack()
        elif self.phase == "attack":
            self._settle_attack()
        elif self.phase == "defend":
            self._settle_defence()
        elif self.phase == "tie_break":
            self._settle_tie()
        elif self.phase == "resolve":
            self._after_resolve()
        elif self.phase == "team_result":
            self._apply_payout()
        elif self.phase == "payout":
            self.phase = "done"

    def _begin_attack(self) -> None:
        self.votes = {}
        self.attack_card = None
        self.defence_card = None
        self.last_result = None
        self.phase = "attack"
        self._arm()

    def _settle_attack(self) -> None:
        options = self._attack_options()
        chosen, tied = self._vote_outcome(options)
        if chosen is None:
            assert tied is not None
            self._start_tie(tied, after="attack")
            return

        self.attack_card = chosen
        self.budgets[self._attacker()] -= cfg.ATTACK_CARDS[chosen]["cost"]
        self.spent.add(chosen)
        self.votes = {}
        self.phase = "defend"
        self._arm()

    def _settle_defence(self) -> None:
        options = self._defence_options()
        chosen, tied = self._vote_outcome(options)
        if chosen is None:
            assert tied is not None
            self._start_tie(tied, after="defend")
            return

        self.defence_card = chosen
        self.budgets[self._defender()] -= cfg.DEFENCE_CARDS[chosen]["cost"]
        self._apply_outcome()
        self.votes = {}
        self.phase = "resolve"
        self._arm()

    def _settle_tie(self) -> None:
        if self._after_tie == "attack":
            deck = cfg.ATTACK_CARDS
        else:
            deck = cfg.DEFENCE_CARDS
        chosen, _ = self._vote_outcome(self.tie_candidates)
        if chosen is None:
            chosen = self._cheapest(self.tie_candidates, deck)

        if self._after_tie == "attack":
            self.attack_card = chosen
            self.budgets[self._attacker()] -= deck[chosen]["cost"]
            self.spent.add(chosen)
            self.votes = {}
            self.phase = "defend"
        else:
            self.defence_card = chosen
            self.budgets[self._defender()] -= deck[chosen]["cost"]
            self._apply_outcome()
            self.votes = {}
            self.phase = "resolve"
        self.tie_candidates = []
        self._after_tie = None
        self._arm()

    def _apply_outcome(self) -> None:
        outcome = cfg.EFFECT_MATRIX[self.attack_card][self.defence_card]
        attack_spec = cfg.ATTACK_CARDS[self.attack_card]
        defence_spec = cfg.DEFENCE_CARDS[self.defence_card]
        attacker = self._attacker()
        defender = self._defender()

        if outcome == "blocked":
            delta_attack = 0
            delta_defence = defence_spec["success"]
        elif outcome == "failed":
            delta_attack = attack_spec["success"]
            delta_defence = defence_spec["fail"]
        else:  # partial
            delta_attack = 0
            delta_defence = 0

        self.scores[attacker] += delta_attack
        self.scores[defender] += delta_defence
        self.last_result = {
            "round": self.round,
            "attacker": attacker,
            "defender": defender,
            "attack_card": self.attack_card,
            "defence_card": self.defence_card,
            "outcome": outcome,
            "attack_delta": delta_attack,
            "defence_delta": delta_defence,
        }

    def _after_resolve(self) -> None:
        if self.round < len(cfg.SCENARIOS):
            self.round += 1
            self._begin_attack()
        else:
            self.phase = "team_result"
            self._arm()

    def _apply_payout(self) -> None:
        winner = "red" if self.scores["red"] >= self.scores["blue"] else "blue"
        self.scores[winner] += cfg.WINNER_BONUS
        self.last_result = {**(self.last_result or {}), "winner": winner, "payout": cfg.WINNER_BONUS}
        self.phase = "payout"
        self._arm()

    def snapshot(self) -> dict:
        """Everything the screens need to render the current moment."""
        if self.paused:
            remaining = max(0.0, self._remaining)
        elif self._deadline is not None:
            remaining = max(0.0, self._deadline - time.monotonic())
        else:
            remaining = 0.0

        scenario = self._scenario() if self.round <= len(cfg.SCENARIOS) else None
        return {
            "code": self.code,
            "phase": self.phase,
            "paused": self.paused,
            "round": self.round,
            "attacker": scenario["attacker"] if scenario else None,
            "defender": scenario["defender"] if scenario else None,
            "remaining_ms": int(remaining * 1000),
            "budgets": dict(self.budgets),
            "scores": dict(self.scores),
            "spent": sorted(self.spent),
            "scenario": scenario,
            "voting_team": self._voting_team() or None,
            "options": self._options_for(self._voting_team()),
            "tie_candidates": list(self.tie_candidates),
            "attack_card": self.attack_card,
            "defence_card": self.defence_card,
            "last_result": self.last_result,
            # Full card decks, so the screens render names, costs and
            # descriptions without keeping their own copy of the rules.
            "cards": {"attack": cfg.ATTACK_CARDS, "defence": cfg.DEFENCE_CARDS},
        }

    def _options_for(self, team: str) -> list[str]:
        if self.phase == "attack":
            return self._attack_options()
        if self.phase == "defend":
            return self._defence_options()
        if self.phase == "tie_break":
            return list(self.tie_candidates)
        return []

    # ─── the clock ───────────────────────────────────────────────────────────
    def _ensure_runner(self) -> None:
        if self._runner is None or self._runner.done():
            self._wake = asyncio.Event()
            self._runner = asyncio.create_task(self._run(), name=f"sabotage-{self.code}")

    async def _run(self) -> None:
        try:
            while self.phase != "done":
                if self.paused:
                    assert self._wake is not None
                    await self._wake.wait()
                    self._wake.clear()
                    continue
                if self._deadline is None:
                    self._deadline = time.monotonic() + cfg.TIMINGS.get(self.phase, 5)
                timeout = max(0.0, self._deadline - time.monotonic())
                try:
                    assert self._wake is not None
                    await asyncio.wait_for(self._wake.wait(), timeout=timeout)
                    self._wake.clear()
                    continue
                except TimeoutError:
                    pass
                self._deadline = None
                self.advance()
                await self._notify()
            # The game just ended: persist the team scores exactly once.
            if self._on_finish is not None:
                await self._on_finish()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let the show die silently
            logger.exception("sabotage engine crashed")
            self.phase = "done"
            await self._notify()

    async def run_async(self) -> None:
        """Kick off the clock after `start()` and broadcast once."""
        self._ensure_runner()
        assert self._wake is not None
        self._wake.set()
        await self._notify()

    # ─── pause / resume, mirroring the main engine ───────────────────────────
    def pause(self) -> None:
        """Freeze the current phase's remaining time."""
        if self.paused or self.phase in ("setup", "done"):
            return
        if self._deadline is not None:
            self._remaining = max(0.0, self._deadline - time.monotonic())
            self._deadline = None
        self.paused = True
        if self._wake is not None:
            self._wake.set()

    def resume(self) -> None:
        if not self.paused:
            return
        self.paused = False
        if self.phase not in ("setup", "done"):
            self._deadline = time.monotonic() + (self._remaining or cfg.TIMINGS.get(self.phase, 5))
        if self._wake is not None:
            self._wake.set()


class SabotageRegistry:
    """One engine per room, kept in memory like the room engines."""

    def __init__(self) -> None:
        self._engines: dict[str, SabotageEngine] = {}

    def ensure(self, code: str) -> SabotageEngine:
        engine = self._engines.get(code)
        if engine is None:
            engine = SabotageEngine(code=code)
            self._engines[code] = engine
        return engine

    def get(self, code: str) -> SabotageEngine | None:
        return self._engines.get(code)

    def remove(self, code: str) -> None:
        engine = self._engines.pop(code, None)
        if engine is not None and engine._runner is not None and not engine._runner.done():
            engine._runner.cancel()


sabotages = SabotageRegistry()


# ─── routes ──────────────────────────────────────────────────────────────────
from fastapi import APIRouter, Depends, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.auth import Claims, require_admin, require_player  # noqa: E402
from app.database import SessionLocal, get_session  # noqa: E402
from app.engine import Phase  # noqa: E402
from app.manager import manager  # noqa: E402
from app.models import Player, Score  # noqa: E402

router = APIRouter(prefix="/sabotage", tags=["sabotage"])


def _wire(code: str, engine: SabotageEngine) -> None:
    """Wire a room's broadcast and the end-of-game score write, once each."""
    if not engine._wired:
        async def broadcast_sabotage() -> None:
            await manager.broadcast_to(
                code, {"type": "sabotage", "payload": engine.snapshot()}
            )

        engine.subscribe(broadcast_sabotage)
        engine._wired = True

    async def settle_scores() -> None:
        """Persist each player's team score into `scores.sabotage_score`.

        Every competing member of a team shares the team total, so the write is
        the same value for everyone on a side. Players never report it
        themselves: the engine computes it and this callback lands it.
        """
        async with SessionLocal() as session:
            rows = (
                await session.execute(select(Player).where(Player.room_code == code))
            ).scalars().all()
            for player in rows:
                if player.team not in ("red", "blue"):
                    continue
                score = await session.get(Score, player.id)
                if score is None:
                    score = Score(player_id=player.id)
                    session.add(score)
                score.sabotage_score = engine.scores[player.team]
            await session.commit()

    engine._on_finish = settle_scores


async def auto_start_sabotage(code: str, main_engine) -> bool:
    """Start Sabotage the moment the main flow reaches its node.

    Called from the room's broadcast listener on every state change, so it must
    be idempotent: it only acts when the flow is playing the sabotage node and
    the sabotage engine is still in `setup`. Teams are assigned randomly if the
    host has not done it, so the whole task runs with zero operator input.

    A room with a single player cannot field two teams. Rather than parking the
    show on a node nobody can play for its full slot, that player is paid
    `cfg.SOLO_SCORE` and the flow is moved straight past the node.
    """
    node = main_engine._node() or {}
    if node.get("game") != "sabotage":
        return False
    if main_engine.phase is not Phase.PLAYING:
        return False
    existing = sabotages.get(code)
    if existing is not None and existing.phase != "setup":
        return False

    async with SessionLocal() as session:
        players = (
            await session.execute(
                select(Player).where(Player.room_code == code).order_by(Player.id)
            )
        ).scalars().all()
        solo = len(players) == 1
        red: set[int] = set()
        blue: set[int] = set()
        if solo:
            # No teams at all: there is nobody to put on the other side, and a
            # leftover side on the player would only confuse the screens.
            await _pay_solo(session, players[0])
        else:
            ids = [player.id for player in players]
            random.shuffle(ids)
            split = (len(ids) + 1) // 2  # odd rooms give Red the extra player
            red = set(ids[:split])
            blue = set(ids[split:])
            for player in players:
                player.team = "red" if player.id in red else "blue"
        # Commit before anything is broadcast: a player's device re-reads
        # /api/auth/me the instant the reveal appears and must find its side
        # already written. Never move the reveal above this line.
        await session.commit()

    if solo:
        # Outside the session on purpose: advancing the flow re-enters this
        # listener, which opens a session of its own.
        await _skip_sabotage_node(main_engine)
        return False
    if not red or not blue:
        return False

    engine = sabotages.ensure(code)
    engine.teams = {"red": red, "blue": blue}
    _wire(code, engine)
    engine.start()
    await engine.run_async()
    return True


async def _pay_solo(session: AsyncSession, player: Player) -> None:
    """Pay a lone player the perfect winning score.

    Setting the column rather than adding to it keeps this idempotent: the
    listener runs again on every state change while the flow is on this node.
    """
    score = await session.get(Score, player.id)
    if score is None:
        score = Score(player_id=player.id)
        session.add(score)
    score.sabotage_score = cfg.SOLO_SCORE


async def _skip_sabotage_node(main_engine) -> None:
    """Move the flow past the Sabotage node instead of playing it."""
    next_index = main_engine.index + 1
    if next_index >= main_engine.node_count:
        return
    logger.info("sabotage skipped: the room has a single player")
    await main_engine.go_to(next_index)


class VoteBody(BaseModel):
    card: str


def _engine(code: str) -> SabotageEngine:
    engine = sabotages.get(code)
    if engine is None:
        raise HTTPException(status_code=409, detail=f"sabotage not started in room: {code}")
    return engine


@router.post(
    "/{code}/start",
    summary="Start Sabotage with the room's current teams",
    dependencies=[Depends(require_admin)],
)
async def start_sabotage(code: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Snapshot the room's Red/Blue split into the engine and begin the reveal.

    The teams come from `players.team`, set by the teams/assign endpoint. A
    player with no team yet simply does not play, and starting refuses unless
    both sides have at least one player.
    """
    rows = (await session.execute(select(Player).where(Player.room_code == code))).scalars().all()
    teams: dict[str, set[int]] = {"red": set(), "blue": set()}
    for player in rows:
        if player.team in ("red", "blue"):
            teams[player.team].add(player.id)

    engine = sabotages.ensure(code)
    engine.teams = teams
    _wire(code, engine)
    try:
        engine.start()
    except SabotageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await engine.run_async()
    return engine.snapshot()


@router.post("/{code}/vote", summary="Cast or change this player's card choice")
async def vote(
    code: str,
    body: VoteBody,
    player: Player = Depends(require_player),
) -> dict:
    engine = _engine(code)
    if player.room_code != code:
        raise HTTPException(status_code=409, detail=f"not in room: {code}")
    try:
        engine.cast_vote(player.id, body.card)
    except SabotageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"card": body.card, "state": engine.snapshot()}


@router.get("/{code}/state", summary="The current Sabotage moment")
async def state(code: str) -> dict:
    """Public like the room state: the screens read it to render, and it never
    exposes how individuals voted."""
    return _engine(code).snapshot()
