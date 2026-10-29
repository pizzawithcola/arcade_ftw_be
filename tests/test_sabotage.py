"""The Sabotage engine: votes, ties, budget, the spent deck and scoring.

Engine logic is tested synchronously by calling `advance()` directly, so a
whole show can be played through instantly. The API layer is tested separately
and only exercises the thin shell around this state machine.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app import sabotage_config as cfg
from app.auth import ADMIN_SUBJECT, SCOPE_FULL, create_token
from app.main import app
from app.flow import ROUND_FLOW
from app.sabotage import SabotageEngine, SabotageError, sabotages

# Derived, not hardcoded: inserting a node into the rundown used to break these
# tests with an index that pointed at whatever had taken the old slot.
SABOTAGE_INDEX = next(i for i, node in enumerate(ROUND_FLOW) if node.get("game") == "sabotage")


def make_engine(code: str = "sab-eng") -> SabotageEngine:
    engine = SabotageEngine(code=code)
    engine.teams = {"red": {1, 2}, "blue": {3, 4}}
    return engine


def vote_team(engine: SabotageEngine, team: str, card: str) -> None:
    for player_id in engine.teams[team]:
        engine.cast_vote(player_id, card)


# ─── engine: scoring matrix ──────────────────────────────────────────────────
def test_blocked_credits_the_defender_only() -> None:
    engine = make_engine()
    engine.start()
    engine.advance()  # -> attack (red)
    engine.attack_card = "deepfake_video"
    engine.defence_card = "verify_independently"
    engine._apply_outcome()
    # blocked: defender success, attacker zero.
    assert engine.scores["red"] == 0
    assert engine.scores["blue"] == cfg.DEFENCE_CARDS["verify_independently"]["success"]


def test_failed_credits_the_attacker_and_penalises_the_defender() -> None:
    engine = make_engine()
    engine.start()
    engine.advance()
    engine.attack_card = "ai_agent_attack"
    engine.defence_card = "investigate_further"
    engine._apply_outcome()
    assert engine.scores["red"] == cfg.ATTACK_CARDS["ai_agent_attack"]["success"]
    assert engine.scores["blue"] == cfg.DEFENCE_CARDS["investigate_further"]["fail"]


def test_partial_credits_neither_side() -> None:
    engine = make_engine()
    engine.start()
    engine.advance()
    engine.attack_card = "personalised_message"
    engine.defence_card = "verify_independently"
    engine._apply_outcome()
    assert engine.scores["red"] == 0
    assert engine.scores["blue"] == 0


def test_every_attack_has_a_blocking_defence_and_matrix_is_complete() -> None:
    defences = set(cfg.DEFENCE_CARDS)
    for attack, row in cfg.EFFECT_MATRIX.items():
        assert attack in cfg.ATTACK_CARDS
        assert set(row) == defences, f"{attack} matrix does not cover every defence"
        assert "blocked" in row.values(), f"{attack} has no blocking defence"
    # Continue Carefully never blocks anything.
    for row in cfg.EFFECT_MATRIX.values():
        assert row["continue_carefully"] != "blocked"


# ─── engine: the full three rounds ───────────────────────────────────────────
def test_a_full_show_runs_three_rounds_and_finishes() -> None:
    engine = make_engine()
    engine.start()
    assert engine.phase == "team_reveal"

    engine.advance()  # reveal -> r1 attack (red)
    assert engine.phase == "attack" and engine._attacker() == "red"
    vote_team(engine, "red", "personalised_message")
    engine.advance()  # settle -> defend (blue)
    assert engine.phase == "defend"
    vote_team(engine, "blue", "verify_independently")
    engine.advance()  # settle -> resolve
    assert engine.phase == "resolve"
    engine.advance()  # -> r2 attack (blue)
    assert engine.round == 2 and engine._attacker() == "blue"

    vote_team(engine, "blue", "cloned_voice")
    engine.advance()
    vote_team(engine, "red", "stop_the_action")
    engine.advance()
    engine.advance()  # -> r3 attack (red)
    assert engine.round == 3

    vote_team(engine, "red", "ai_agent_attack")
    engine.advance()
    vote_team(engine, "blue", "continue_carefully")
    engine.advance()
    engine.advance()  # resolve -> team_result
    assert engine.phase == "team_result"
    engine.advance()  # -> payout
    assert engine.phase == "payout"
    engine.advance()  # -> done
    assert engine.phase == "done"
    # Three attacks were played, three spent, three remain.
    assert len(engine.spent) == 3


def test_attacks_are_spent_globally_after_use() -> None:
    engine = make_engine()
    engine.start()
    engine.advance()  # r1 attack (red)
    vote_team(engine, "red", "deepfake_video")
    engine.advance()  # settle -> defend
    assert "deepfake_video" in engine.spent
    assert "deepfake_video" not in engine._attack_options()

    # Round 2: Blue attacks and cannot pick the card Red already used.
    vote_team(engine, "blue", "continue_carefully")
    engine.advance()
    engine.advance()  # -> r2 attack (blue)
    assert "deepfake_video" not in engine._attack_options()
    with pytest.raises(SabotageError):
        engine.cast_vote(3, "deepfake_video")


# ─── engine: budget ──────────────────────────────────────────────────────────
def test_a_card_the_team_cannot_afford_is_refused() -> None:
    engine = make_engine()
    engine.start()
    engine.budgets = {"red": 5_000, "blue": 5_000}
    engine.advance()  # attack (red)
    # The cheapest attack is 10k, so nothing is affordable.
    assert engine._attack_options() == []
    with pytest.raises(SabotageError):
        engine.cast_vote(1, "ai_written_message")


def test_affordable_defence_costs_are_deducted_once() -> None:
    engine = make_engine()
    engine.start()
    engine.advance()
    vote_team(engine, "red", "deepfake_video")
    engine.advance()  # defend (blue), budget still 100k
    vote_team(engine, "blue", "verify_independently")
    engine.advance()  # settle defence
    assert engine.budgets["blue"] == cfg.STARTING_BUDGET - cfg.DEFENCE_CARDS["verify_independently"]["cost"]


# ─── engine: voting and ties ─────────────────────────────────────────────────
def test_only_the_acting_team_can_vote() -> None:
    engine = make_engine()
    engine.start()
    engine.advance()  # attack (red)
    with pytest.raises(SabotageError):
        engine.cast_vote(3, "deepfake_video")  # blue player during red's attack


def test_a_tie_revotes_among_the_tied_cards_then_breaks() -> None:
    engine = make_engine()
    engine.start()
    engine.advance()  # attack (red), two players
    # Split the vote: a tie between two cards.
    engine.cast_vote(1, "deepfake_video")
    engine.cast_vote(2, "cloned_voice")
    engine.advance()  # settle -> tie_break
    assert engine.phase == "tie_break"
    assert set(engine.tie_candidates) == {"deepfake_video", "cloned_voice"}

    # Only the tied cards are acceptable during the revote.
    with pytest.raises(SabotageError):
        engine.cast_vote(1, "ai_agent_attack")

    engine.cast_vote(1, "cloned_voice")
    engine.cast_vote(2, "cloned_voice")
    engine.advance()  # break tie -> defend
    assert engine.phase == "defend"
    assert engine.attack_card == "cloned_voice"
    assert engine.attack_card in engine.spent


def test_a_tie_that_never_breaks_picks_the_cheapest_card() -> None:
    engine = make_engine()
    engine.start()
    engine.advance()
    engine.cast_vote(1, "deepfake_video")  # 30k
    engine.cast_vote(2, "ai_written_message")  # 10k
    engine.advance()  # -> tie_break
    assert engine.phase == "tie_break"
    # Nobody votes again; the revote ends and picks the cheaper card.
    engine.advance()
    assert engine.phase == "defend"
    assert engine.attack_card == "ai_written_message"


# ─── engine: payout ──────────────────────────────────────────────────────────
def test_the_winner_gets_the_bonus() -> None:
    engine = make_engine()
    engine.start()
    engine.scores = {"red": 900, "blue": 300}
    engine.round = 3
    engine.phase = "team_result"
    engine._apply_payout()
    assert engine.scores["red"] == 900 + cfg.WINNER_BONUS
    assert engine.scores["blue"] == 300
    assert engine.phase == "payout"


# ─── API shell ───────────────────────────────────────────────────────────────
@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer " + create_token(ADMIN_SUBJECT, SCOPE_FULL)}


async def _login(client: AsyncClient, nickname: str) -> tuple[dict[str, str], int]:
    response = await client.post(
        "/api/auth/player", json={"nickname": nickname, "event": "hk_employee"}
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}, response.json()["player_id"]


async def test_start_refuses_when_a_team_is_empty(
    client: AsyncClient, admin_headers: dict
) -> None:
    created = await client.post("/api/rooms", json={"code": "sab-empty"}, headers=admin_headers)
    assert created.status_code == 201
    response = await client.post("/api/sabotage/sab-empty/start", headers=admin_headers)
    assert response.status_code == 409
    sabotages.remove("sab-empty")


async def test_start_enters_the_reveal(client: AsyncClient, admin_headers: dict) -> None:
    await client.post("/api/rooms", json={"code": "sab-start"}, headers=admin_headers)
    for name in ("One", "Two", "Three", "Four"):
        headers, _ = await _login(client, name)
        await client.post("/api/rooms/sab-start/join", headers=headers)
    await client.post("/api/rooms/sab-start/teams/assign", headers=admin_headers)

    response = await client.post("/api/sabotage/sab-start/start", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["phase"] == "team_reveal"

    state = await client.get("/api/sabotage/sab-start/state")
    assert state.status_code == 200
    assert state.json()["phase"] == "team_reveal"

    # Voting is not open yet, so a player vote is refused.
    player, _ = await _login(client, "One")
    voted = await client.post(
        "/api/sabotage/sab-start/vote", json={"card": "deepfake_video"}, headers=player
    )
    assert voted.status_code == 409
    sabotages.remove("sab-start")


async def test_vote_accepts_the_acting_teams_choice(
    client: AsyncClient, admin_headers: dict
) -> None:
    await client.post("/api/rooms", json={"code": "sab-vote"}, headers=admin_headers)
    players: dict[str, tuple[dict[str, str], int]] = {}
    for name in ("One", "Two", "Three", "Four"):
        players[name] = await _login(client, name)
        await client.post("/api/rooms/sab-vote/join", headers=players[name][0])
    await client.post("/api/rooms/sab-vote/teams/assign", headers=admin_headers)
    await client.post("/api/sabotage/sab-vote/start", headers=admin_headers)

    # Move the engine to the first attack phase synchronously.
    engine = sabotages.get("sab-vote")
    assert engine is not None
    engine.advance()  # reveal -> attack (red)

    # Red players are ids 1 and 2 after the deterministic split.
    voted = await client.post(
        "/api/sabotage/sab-vote/vote", json={"card": "personalised_message"}, headers=players["One"][0]
    )
    assert voted.status_code == 200
    assert voted.json()["card"] == "personalised_message"
    assert engine.votes[players["One"][1]] == "personalised_message"

    # A blue player cannot vote during red's attack. After the alternating
    # split, ids 2 and 4 are Blue, so "Two" is on the defending side.
    blue = await client.post(
        "/api/sabotage/sab-vote/vote", json={"card": "deepfake_video"}, headers=players["Two"][0]
    )
    assert blue.status_code == 409
    sabotages.remove("sab-vote")


async def test_vote_requires_a_player_token(client: AsyncClient, admin_headers: dict) -> None:
    await client.post("/api/rooms", json={"code": "sab-auth"}, headers=admin_headers)
    await client.post("/api/sabotage/sab-auth/start", headers=admin_headers)
    response = await client.post(
        "/api/sabotage/sab-auth/vote", json={"card": "deepfake_video"}
    )
    assert response.status_code == 401
    sabotages.remove("sab-auth")


# ─── pause / resume, mirroring the main engine ───────────────────────────────
def test_pause_freezes_the_clock_and_resume_restores_it() -> None:
    engine = make_engine()
    engine.start()
    assert engine.phase == "team_reveal"

    engine.pause()
    assert engine.paused is True
    assert engine._deadline is None
    assert engine._remaining > 0
    assert engine.snapshot()["paused"] is True
    assert engine.snapshot()["remaining_ms"] > 0

    engine.resume()
    assert engine.paused is False
    assert engine._deadline is not None


def test_a_paused_show_refuses_votes() -> None:
    engine = make_engine()
    engine.start()
    engine.advance()  # -> attack (red)
    engine.pause()
    with pytest.raises(SabotageError):
        engine.cast_vote(1, "deepfake_video")


async def test_pausing_the_room_pauses_sabotage(
    client: AsyncClient, admin_headers: dict
) -> None:
    from app.rooms import rooms

    await client.post("/api/rooms", json={"code": "sab-pause"}, headers=admin_headers)
    for name in ("One", "Two", "Three", "Four"):
        headers, _ = await _login(client, name)
        await client.post("/api/rooms/sab-pause/join", headers=headers)
    await client.post("/api/rooms/sab-pause/teams/assign", headers=admin_headers)

    started = await client.post("/api/sabotage/sab-pause/start", headers=admin_headers)
    assert started.status_code == 200

    # Drive the show into playing and pause it; Sabotage must freeze with it.
    await client.post("/api/rooms/sab-pause/start", headers=admin_headers)
    paused = await client.post("/api/rooms/sab-pause/pause", headers=admin_headers)
    assert paused.status_code == 200

    state = await client.get("/api/sabotage/sab-pause/state")
    assert state.json()["paused"] is True

    resumed = await client.post("/api/rooms/sab-pause/resume", headers=admin_headers)
    assert resumed.status_code == 200
    state = await client.get("/api/sabotage/sab-pause/state")
    assert state.json()["paused"] is False

    engine = rooms.get("sab-pause")
    if engine is not None:
        await engine.finish()
    sabotages.remove("sab-pause")


# ─── M5: auto-start and score settlement ─────────────────────────────────────
async def test_auto_start_when_the_flow_reaches_the_sabotage_node(
    client: AsyncClient, admin_headers: dict
) -> None:
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Player
    from app.rooms import rooms
    from app.sabotage import sabotages

    await client.post("/api/rooms", json={"code": "sab-auto"}, headers=admin_headers)
    for name in ("One", "Two", "Three", "Four"):
        headers, _ = await _login(client, name)
        await client.post("/api/rooms/sab-auto/join", headers=headers)

    # The main flow lands on the Sabotage node and the broadcast listener
    # starts the game.
    engine = rooms.get("sab-auto")
    assert engine is not None
    await engine.go_to(SABOTAGE_INDEX)

    sab = sabotages.get("sab-auto")
    assert sab is not None
    assert sab.phase == "team_reveal"

    async with SessionLocal() as session:
        players = (
            await session.execute(select(Player).where(Player.room_code == "sab-auto"))
        ).scalars().all()
        teams = {player.team for player in players}
        assert teams == {"red", "blue"}

    await engine.finish()
    sabotages.remove("sab-auto")


async def test_every_jump_back_to_node_5_reassigns_teams(
    client: AsyncClient, admin_headers: dict
) -> None:
    """The operator rehearses by jumping away and back: each landing on the
    Sabotage node is a brand new game, teams re-assigned fresh."""
    from app.rooms import rooms
    from app.sabotage import sabotages

    await client.post("/api/rooms", json={"code": "sab-rejump"}, headers=admin_headers)
    for name in ("One", "Two"):
        headers, _ = await _login(client, name)
        await client.post("/api/rooms/sab-rejump/join", headers=headers)

    engine = rooms.get("sab-rejump")
    assert engine is not None

    # First landing (via the operator's jump endpoint): a fresh game.
    await client.post(f"/api/rooms/sab-rejump/go/{SABOTAGE_INDEX}", headers=admin_headers)
    first = sabotages.get("sab-rejump")
    assert first is not None
    assert first.phase == "team_reveal"

    # Jump away, then back: the previous game is discarded and teams re-roll.
    await client.post("/api/rooms/sab-rejump/go/0", headers=admin_headers)
    assert sabotages.get("sab-rejump") is None

    await client.post(f"/api/rooms/sab-rejump/go/{SABOTAGE_INDEX}", headers=admin_headers)
    second = sabotages.get("sab-rejump")
    assert second is not None
    assert second is not first  # a new engine, not the resumed one
    assert second.phase == "team_reveal"

    from app.database import SessionLocal
    from sqlalchemy import select

    from app.models import Player

    async with SessionLocal() as session:
        players = (
            await session.execute(select(Player).where(Player.room_code == "sab-rejump"))
        ).scalars().all()
        teams = {player.team for player in players}
        assert teams == {"red", "blue"}

    await engine.finish()
    sabotages.remove("sab-rejump")


async def test_a_solo_room_is_paid_and_the_game_is_skipped(
    client: AsyncClient, admin_headers: dict
) -> None:
    from app.database import SessionLocal
    from app.models import Player, Score
    from app.rooms import rooms
    from app.sabotage import sabotages

    await client.post("/api/rooms", json={"code": "sab-solo"}, headers=admin_headers)
    headers, player_id = await _login(client, "Solo")
    await client.post("/api/rooms/sab-solo/join", headers=headers)

    engine = rooms.get("sab-solo")
    assert engine is not None
    await engine.go_to(SABOTAGE_INDEX)

    # One player cannot field two teams: no game runs, and the flow has already
    # moved past the node instead of waiting out its 170-second slot.
    assert sabotages.get("sab-solo") is None
    assert engine.index == SABOTAGE_INDEX + 1
    assert (engine._node() or {}).get("game") != "sabotage"

    async with SessionLocal() as session:
        score = await session.get(Score, player_id)
        assert score is not None
        assert score.sabotage_score == cfg.SOLO_SCORE
        player = await session.get(Player, player_id)
        assert player is not None
        # A skipped game assigns nobody a side.
        assert player.team is None

    await engine.finish()


async def test_finish_persists_each_members_team_score(
    client: AsyncClient, admin_headers: dict
) -> None:
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Player, Score
    from app.sabotage import _wire, sabotages

    await client.post("/api/rooms", json={"code": "sab-settle"}, headers=admin_headers)
    ids: dict[str, int] = {}
    for name in ("One", "Two", "Three", "Four"):
        headers, pid = await _login(client, name)
        await client.post("/api/rooms/sab-settle/join", headers=headers)
        ids[name] = pid

    engine = sabotages.ensure("sab-settle")
    engine.teams = {"red": {ids["One"], ids["Two"]}, "blue": {ids["Three"], ids["Four"]}}

    # The engine reads the teams from the database when it settles, so write
    # the same split back to the rows.
    async with SessionLocal() as session:
        rows = (
            await session.execute(select(Player).where(Player.room_code == "sab-settle"))
        ).scalars().all()
        for player in rows:
            player.team = "red" if player.id in engine.teams["red"] else "blue"
        await session.commit()

    _wire("sab-settle", engine)
    engine.start()
    engine.scores = {"red": 500, "blue": 300}
    assert engine._on_finish is not None
    await engine._on_finish()

    async with SessionLocal() as session:
        for name, team in (("One", "red"), ("Two", "red"), ("Three", "blue"), ("Four", "blue")):
            score = await session.get(Score, ids[name])
            assert score is not None
            assert score.sabotage_score == engine.scores[team]

    sabotages.remove("sab-settle")
