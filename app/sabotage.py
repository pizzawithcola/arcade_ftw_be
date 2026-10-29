"""The Sabotage v2 engine: one per room, in memory like the show engine.

Four rounds, each with its own deck. Every card is a vector over three domains
(data, people, infra); every round has one target domain. Each team builds a
hand of up to four cards inside a 100-point budget, and the two builds are
compared column by column when the round ends.

The show inside the game:

    team_reveal -> brief -> [select -> reveal] x4 -> final -> done

Teams are drawn when the flow reaches this node (see `auto_start_sabotage`),
which is what makes the reveal the first thing a player sees.

Votes are a set of cards per player. At the end of the select phase the team's
set is aggregated: most-voted first, ties broken by strength in the round's
target (never by price, which would reward voting for expensive cards), then
affordable-first within the budget and the four-card limit. Cards that do not
fit are skipped, so a team still gets the best build its votes can pay for.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app import sabotage_config as cfg

logger = logging.getLogger(__name__)

TEAMS = ("red", "blue")

PHASES = (
    "setup",
    "team_reveal",
    "brief",
    "select",
    "reveal",
    "final",
    "done",
)


class SabotageError(Exception):
    """A player or host action that the current phase cannot accept."""


# --- voting aggregation -------------------------------------------------------
def aggregate(
    votes: dict[int, list[str]],
    deck: list[str],
    target: str,
    fill: bool = True,
) -> tuple[list[str], list[dict]]:
    """Turn a team's votes into one build.

    Returns the chosen cards and a log of what was dropped and why, because
    "I voted for it, why was it not bought" is the one question a player will
    ask during a 30-second phase.

    Pure and deterministic, so the tests can pin it.
    """
    allowed = set(deck)
    counts: Counter[str] = Counter()
    for card_ids in votes.values():
        for card_id in card_ids:
            if card_id in allowed:
                counts[card_id] += 1

    target_index = cfg.DOMAINS.index(target)
    ranked = sorted(
        counts,
        key=lambda card: (
            -counts[card],
            -cfg.CARDS[card]["vector"][target_index],
            cfg.CARDS[card]["cost"],
        ),
    )

    chosen: list[str] = []
    log: list[dict] = []
    spent = 0
    for card in ranked:
        cost = cfg.CARDS[card]["cost"]
        if len(chosen) >= cfg.MAX_CARDS:
            log.append({"card": card, "reason": "slots"})
            continue
        if spent + cost > cfg.BUDGET:
            log.append({"card": card, "reason": "budget"})
            continue
        chosen.append(card)
        spent += cost

    if not chosen and fill:
        # Nobody voted, or nothing voted for was affordable. Fall back to the
        # strongest affordable hand on the target so a team that stayed quiet
        # is not handed a zero by accident. `fill=False` while the phase is
        # still open: an auto-filled preview would tell the team it had already
        # decided something.
        for card in sorted(
            deck,
            key=lambda card: (-cfg.CARDS[card]["vector"][target_index], cfg.CARDS[card]["cost"]),
        ):
            cost = cfg.CARDS[card]["cost"]
            if len(chosen) >= cfg.MAX_CARDS or spent + cost > cfg.BUDGET:
                continue
            chosen.append(card)
            spent += cost

    return chosen, log


# --- the engine ---------------------------------------------------------------
@dataclass
class SabotageEngine:
    code: str

    phase: str = "setup"
    # 0-based index into cfg.ROUNDS.
    round: int = 0

    teams: dict[str, set[int]] = field(default_factory=lambda: {"red": set(), "blue": set()})
    scores: dict[str, int] = field(default_factory=lambda: {"red": 0, "blue": 0})
    rounds_won: dict[str, int] = field(default_factory=lambda: {"red": 0, "blue": 0})

    # player id -> the cards that player wants this round
    votes: dict[int, list[str]] = field(default_factory=dict)
    # The locked build each side plays, filled when `select` ends.
    picks: dict[str, list[str]] = field(default_factory=lambda: {"red": [], "blue": []})
    dropped: dict[str, list[dict]] = field(default_factory=lambda: {"red": [], "blue": []})
    # Every finished round, so the final screen can replay them.
    history: list[dict] = field(default_factory=list)
    last_result: dict | None = None

    # --- clock, same discipline as the main engine ---
    _deadline: float | None = None
    _runner: asyncio.Task | None = None
    _wake: asyncio.Event | None = None
    _listeners: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    paused: bool = False
    _remaining: float = 0.0
    _wired: bool = False
    _on_finish: Callable[[], Awaitable[None]] | None = None

    # --- observers ---
    def subscribe(self, listener: Callable[[], Awaitable[None]]) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    async def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                await listener()
            except Exception:  # noqa: BLE001 - one bad listener must not stall the show
                logger.exception("sabotage listener failed")

    # --- round helpers ---
    def round_spec(self) -> dict:
        return cfg.ROUNDS[min(self.round, len(cfg.ROUNDS) - 1)]

    def _role(self, team: str) -> str:
        spec = self.round_spec()
        return "attacker" if team == spec["attacker"] else "defender"

    def deck_for(self, team: str) -> list[str]:
        return cfg.cards_for(self.round, self._role(team))

    def _duration(self) -> float:
        if self.phase == "select" and self.round == 0:
            return cfg.TIMINGS["select_first"]
        return cfg.TIMINGS.get(self.phase, 5)

    def _arm(self) -> None:
        self._deadline = time.monotonic() + self._duration()

    # --- live aggregation, so the bars move while people vote ---
    def team_votes(self, team: str) -> dict[int, list[str]]:
        return {
            player_id: cards
            for player_id, cards in self.votes.items()
            if player_id in self.teams[team]
        }

    def live_build(self, team: str) -> tuple[list[str], list[dict]]:
        """The build the team's votes point at right now, without the
        end-of-phase fallback: an empty bar is honest about having no votes."""
        spec = self.round_spec()
        return aggregate(
            self.team_votes(team), self.deck_for(team), spec["target"], fill=False
        )

    def team_view(self, team: str) -> dict:
        """What one team's own devices may see while everybody is choosing.

        The broadcast is public, so it cannot carry a live build: the other side
        reads the same message. Each device asks for its own team instead, which
        keeps the two builds simultaneous until the reveal.
        """
        build, dropped = self.live_build(team)
        return {
            "team": team,
            "role": self._role(team),
            "round": self.round + 1,
            "target": self.round_spec()["target"],
            "deck": self.deck_for(team),
            "build": build,
            "budget": cfg.BUDGET - cfg.cost_of(build),
            "vector": list(cfg.vector_of(build)),
            "votes": self.vote_counts(team),
            "dropped": dropped,
            "voters": len(self.team_votes(team)),
            "members": len(self.teams[team]),
        }

    def vote_counts(self, team: str) -> dict[str, int]:
        allowed = set(self.deck_for(team))
        counts: Counter[str] = Counter()
        for cards in self.team_votes(team).values():
            for card in cards:
                if card in allowed:
                    counts[card] += 1
        return dict(counts)

    # --- lifecycle ---
    def start(self) -> None:
        if not self.teams["red"] or not self.teams["blue"]:
            raise SabotageError("both teams need at least one player")
        self.phase = "team_reveal"
        self._arm()

    def cast_vote(self, player_id: int, cards: list[str]) -> None:
        if self.phase != "select":
            raise SabotageError(f"not taking votes in phase: {self.phase}")
        if self.paused:
            raise SabotageError("the show is paused")
        team = next((side for side in TEAMS if player_id in self.teams[side]), None)
        if team is None:
            raise SabotageError("player has no team")
        unique = list(dict.fromkeys(cards))
        deck = self.deck_for(team)
        if any(card not in deck for card in unique):
            raise SabotageError("card is not in this round's deck")
        if len(unique) > cfg.MAX_CARDS:
            raise SabotageError(f"at most {cfg.MAX_CARDS} cards")
        if cfg.cost_of(unique) > cfg.BUDGET:
            raise SabotageError("over budget")
        self.votes[player_id] = unique

    def _lock_picks(self) -> None:
        spec = self.round_spec()
        for team in TEAMS:
            cards, dropped = aggregate(
                self.team_votes(team), self.deck_for(team), spec["target"]
            )
            self.picks[team] = cards
            self.dropped[team] = dropped
        self.last_result = self._resolve(spec)

    def _resolve(self, spec: dict) -> dict:
        attacker = spec["attacker"]
        defender = spec["defender"]
        attack_vector = cfg.vector_of(self.picks[attacker])
        defence_vector = cfg.vector_of(self.picks[defender])
        result = cfg.score_round(attack_vector, defence_vector, spec["target"])
        self.scores[attacker] += result["attack_points"]
        self.scores[defender] += result["defence_points"]
        winner = attacker if result["broke_target"] else defender
        self.rounds_won[winner] += 1
        record = {
            "round": spec["round"],
            "theme": spec["theme"],
            "target": spec["target"],
            "attacker": attacker,
            "defender": defender,
            "attack": self.picks[attacker],
            "defence": self.picks[defender],
            "attack_vector": list(attack_vector),
            "defence_vector": list(defence_vector),
            "winner": winner,
            "points": {
                attacker: result["attack_points"],
                defender: result["defence_points"],
            },
            **result,
        }
        self.history.append(record)
        return record

    def advance(self) -> None:
        """Move to the next phase. Called by the clock and by the console."""
        if self.phase == "setup":
            return
        if self.phase == "team_reveal":
            self.phase = "brief"
        elif self.phase == "brief":
            self.phase = "select"
        elif self.phase == "select":
            self._lock_picks()
            self.phase = "reveal"
        elif self.phase == "reveal":
            if self.round + 1 < len(cfg.ROUNDS):
                self.round += 1
                self.votes = {}
                self.picks = {"red": [], "blue": []}
                self.dropped = {"red": [], "blue": []}
                self.phase = "select"
            else:
                self.phase = "final"
        elif self.phase == "final":
            self.phase = "done"
            return
        else:
            return
        self._arm()

    # --- snapshot for the screens ---
    def snapshot(self) -> dict:
        if self.paused:
            remaining = max(0.0, self._remaining)
        elif self._deadline is not None:
            remaining = max(0.0, self._deadline - time.monotonic())
        else:
            remaining = 0.0

        spec = self.round_spec()
        # While selecting, no build is public: both sides commit at the same
        # time, and a broadcast cannot be sent to one team only. Each device
        # fetches its own side from /team instead.
        selecting = self.phase == "select"
        return {
            "code": self.code,
            "phase": self.phase,
            "paused": self.paused,
            "round": self.round + 1,
            "rounds": len(cfg.ROUNDS),
            "remaining_ms": int(remaining * 1000),
            "target": spec["target"],
            "theme": spec["theme"],
            "attacker": spec["attacker"],
            "defender": spec["defender"],
            "attack_brief": spec["attack_brief"],
            "defence_brief": spec["defence_brief"],
            "teams": {team: sorted(self.teams[team]) for team in TEAMS},
            "scores": dict(self.scores),
            "rounds_won": dict(self.rounds_won),
            "decks": {
                "red": self.deck_for("red"),
                "blue": self.deck_for("blue"),
            },
            "builds": {} if selecting else {team: list(self.picks[team]) for team in TEAMS},
            "budget": (
                {} if selecting else {team: cfg.BUDGET - cfg.cost_of(self.picks[team]) for team in TEAMS}
            ),
            "vectors": (
                {} if selecting else {team: list(cfg.vector_of(self.picks[team])) for team in TEAMS}
            ),
            "votes": {} if selecting else {team: self.vote_counts(team) for team in TEAMS},
            "dropped": self.dropped if self.phase in ("reveal", "final") else {},
            "cards": cfg.CARDS,
            "domains": list(cfg.DOMAINS),
            "outcome_names": {f"{k[0]}-{k[1]}": v for k, v in cfg.OUTCOME_NAMES.items()},
            "last_result": self.last_result,
            "history": self.history,
        }

    # --- the clock ---
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
                    self._arm()
                timeout = max(0.0, (self._deadline or 0) - time.monotonic())
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

    # --- pause / resume, mirroring the main engine ---
    def pause(self) -> None:
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
            self._deadline = time.monotonic() + (self._remaining or self._duration())
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


# --- routes -------------------------------------------------------------------
from fastapi import APIRouter, Depends, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.auth import require_admin, require_player  # noqa: E402
from app.database import SessionLocal, get_session  # noqa: E402
from app.engine import Phase  # noqa: E402
from app.manager import manager  # noqa: E402
from app.models import Player, Score  # noqa: E402

router = APIRouter(prefix="/sabotage", tags=["sabotage"])


def _wire(code: str, engine: SabotageEngine) -> None:
    """Wire a room's broadcast and the end-of-game score write, once each."""
    if not engine._wired:

        async def broadcast_sabotage() -> None:
            await manager.broadcast_to(code, {"type": "sabotage", "payload": engine.snapshot()})

        engine.subscribe(broadcast_sabotage)
        engine._wired = True

    async def settle_scores() -> None:
        """Persist each player's team score into `scores.sabotage_score`.

        Every member of a team shares the team total. Players never report it
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
    the sabotage engine is still in `setup`. Teams are drawn here, at the intro,
    so the first thing each player sees is which side they are on.

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
    """Pay a lone player the best score a team can reach.

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
    cards: list[str]


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
    """Snapshot the room's Red/Blue split into the engine and begin the reveal."""
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


@router.post("/{code}/vote", summary="Set this player's cards for the round")
async def vote(
    code: str,
    body: VoteBody,
    player: Player = Depends(require_player),
) -> dict:
    engine = _engine(code)
    if player.room_code != code:
        raise HTTPException(status_code=409, detail=f"not in room: {code}")
    try:
        engine.cast_vote(player.id, body.cards)
    except SabotageError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"cards": body.cards, "state": engine.snapshot()}


@router.get("/{code}/team", summary="This player's own side of the current round")
async def team_view(code: str, player: Player = Depends(require_player)) -> dict:
    engine = _engine(code)
    team = next((side for side in TEAMS if player.id in engine.teams[side]), None)
    if team is None:
        raise HTTPException(status_code=409, detail="player has no team")
    return engine.team_view(team)


@router.get("/{code}/state", summary="The current Sabotage moment")
async def state(code: str) -> dict:
    """Public like the room state: the screens read it to render, and it never
    exposes how individuals voted."""
    return _engine(code).snapshot()
