"""The Sabotage v2 engine: card vectors, voting aggregation, scoring, clock.

Engine logic is tested synchronously by calling `advance()` directly, so a
whole show can be played through instantly. The API layer is tested separately
and only exercises the thin shell around this state machine.
"""

from itertools import combinations

import pytest
from httpx import ASGITransport, AsyncClient

from app import sabotage_config as cfg
from app.auth import ADMIN_SUBJECT, SCOPE_FULL, create_token
from app.flow import ROUND_FLOW
from app.main import app
from app.sabotage import SabotageEngine, SabotageError, aggregate, sabotages

# Derived, not hardcoded: inserting a node into the rundown used to break these
# tests with an index that pointed at whatever had taken the old slot.
SABOTAGE_INDEX = next(i for i, node in enumerate(ROUND_FLOW) if node.get("game") == "sabotage")

# Stands in for "money is no object" when a test is about the decks rather than
# the budget: comfortably more than four of the dearest cards.
ANY_MONEY = 10**9


def make_engine(code: str = "sab-eng") -> SabotageEngine:
    engine = SabotageEngine(code=code)
    engine.teams = {"red": {1, 2}, "blue": {3, 4}}
    return engine


def affordable_hands(deck: list[str], target: str, budget: int):
    """Every hand a side could field for `budget`, with its target column."""
    index = cfg.DOMAINS.index(target)
    for size in range(1, cfg.MAX_CARDS + 1):
        for combo in combinations(deck, size):
            cost = cfg.cost_of(combo)
            if cost <= budget:
                yield cfg.vector_of(combo)[index], cost, combo


def best_target(deck: list[str], target: str, budget: int) -> int:
    """The strongest target column a side can afford, by exhaustion."""
    return max(value for value, _, _ in affordable_hands(deck, target, budget))


def cheapest_ceiling(deck: list[str], target: str) -> int:
    """The least money that still reaches the deck's best target column.

    This is the number the match budget is designed around: the round a side
    cannot max out is the round it has to decide to lose.
    """
    ceiling = best_target(deck, target, ANY_MONEY)
    return min(
        cost for value, cost, _ in affordable_hands(deck, target, ANY_MONEY) if value == ceiling
    )


def best_hand(engine: SabotageEngine, team: str) -> list[str]:
    """The cheapest hand that maxes this side's target column with the money
    the team has left, or nothing when even the cheapest card does not fit."""
    deck = engine.deck_for(team)
    target = engine.round_spec()["target"]
    hands = affordable_hands(deck, target, engine.remaining(team))
    scored = list(hands)
    if not scored:
        return []
    ceiling = max(value for value, _, _ in scored)
    cost, hand = min((cost, combo) for value, cost, combo in scored if value == ceiling)
    return list(hand)


def play_round(engine: SabotageEngine) -> None:
    """Both sides field their best affordable hand, as a well-run team would."""
    for team in ("red", "blue"):
        hand = best_hand(engine, team)
        for player_id in engine.teams[team]:
            engine.cast_vote(player_id, hand)


# --- balance ------------------------------------------------------------------
def test_every_round_keeps_the_one_point_margin() -> None:
    """The attacker is exactly one point ahead when both sides play optimally.

    One point is the whole design: spend badly and you do not break through,
    waste a slot on a distraction and the defence holds. Deliberately measured
    without a budget - this is about how the decks are built, and running out of
    money is the separate story the money tests tell.
    """
    for index, spec in enumerate(cfg.ROUNDS):
        attack = best_target(cfg.cards_for(index, "attacker"), spec["target"], ANY_MONEY)
        defence = best_target(cfg.cards_for(index, "defender"), spec["target"], ANY_MONEY)
        assert attack == defence + 1, f"round {spec['round']} is {attack} vs {defence}"


def test_one_round_costs_between_25_and_30_thousand() -> None:
    """A round maxed out has to be a quarter to a third of the match budget.

    Cheaper and the money never bites; dearer and the first round decides the
    match on its own.
    """
    for index, spec in enumerate(cfg.ROUNDS):
        for role in ("attacker", "defender"):
            cost = cheapest_ceiling(cfg.cards_for(index, role), spec["target"])
            assert 25_000 <= cost <= 30_000, f"round {spec['round']} {role} costs ${cost:,}"


def test_four_full_rounds_cost_more_than_the_pool() -> None:
    """The budget has to bite, or "shared" means nothing: both sides can reach
    their ceiling every round, and neither can pay for all four."""
    for team in ("red", "blue"):
        total = 0
        for index, spec in enumerate(cfg.ROUNDS):
            role = "attacker" if spec["attacker"] == team else "defender"
            total += cheapest_ceiling(cfg.cards_for(index, role), spec["target"])
        assert total > cfg.TOTAL_BUDGET, f"{team} can afford every round: ${total:,}"


def test_the_last_round_is_the_one_that_has_to_give() -> None:
    """Three rounds at full strength leave less than the fourth one needs.

    This is the planning story of the whole game, so it is pinned: if it ever
    stops being true the reveal has nothing to reveal.
    """
    engine = make_engine()
    engine.start()
    engine.advance()  # team_reveal -> brief
    engine.advance()  # brief -> select
    for _ in range(len(cfg.ROUNDS) - 1):
        play_round(engine)
        engine.advance()  # select -> reveal
        engine.advance()  # reveal -> next select

    last = len(cfg.ROUNDS) - 1
    for team in ("red", "blue"):
        role = "attacker" if cfg.ROUNDS[last]["attacker"] == team else "defender"
        needs = cheapest_ceiling(cfg.cards_for(last, role), cfg.ROUNDS[last]["target"])
        assert engine.remaining(team) < needs, f"{team} can still afford the last round"
    assert engine.spent("red") + engine.spent("blue") == 161_000


def test_money_is_a_match_pool_not_a_round_allowance() -> None:
    """What round one buys comes out of round four, so the pool only goes down."""
    engine = make_engine()
    engine.start()
    engine.advance()
    engine.advance()
    assert engine.remaining("red") == cfg.TOTAL_BUDGET

    play_round(engine)
    engine.advance()  # select -> reveal
    round_one = engine.spent("red")
    assert round_one == cfg.cost_of(engine.picks["red"])
    assert engine.remaining("red") == cfg.TOTAL_BUDGET - round_one
    assert engine.snapshot()["budget"]["red"] == cfg.TOTAL_BUDGET - round_one



def test_each_team_attacks_twice() -> None:
    """An unbalanced schedule would decide the match before anyone plays: the
    attacker holds the one-point advantage, so whoever attacks more wins."""
    attacks = [spec["attacker"] for spec in cfg.ROUNDS]
    assert attacks.count("red") == len(cfg.ROUNDS) // 2
    assert attacks.count("blue") == len(cfg.ROUNDS) // 2


def test_the_three_targets_all_appear() -> None:
    targets = {spec["target"] for spec in cfg.ROUNDS}
    assert targets == set(cfg.DOMAINS)


def test_every_card_belongs_to_a_deck() -> None:
    """No orphan cards: an entry nothing plays is a card nobody can ever see."""
    used = {card for spec in cfg.ROUNDS for card in spec["attack"] + spec["defence"]}
    assert used == set(cfg.CARDS)


def test_no_deck_lists_the_same_card_twice() -> None:
    """A deck is five different cards. A repeat would be a dead slot on the
    phone, and the four-card limit already caps the hand."""
    for index, spec in enumerate(cfg.ROUNDS):
        for role in ("attacker", "defender"):
            deck = cfg.cards_for(index, role)
            assert len(deck) == len(set(deck)), f"round {spec['round']} {role} repeats a card"
            assert len(deck) > cfg.MAX_CARDS, "a deck must hold more cards than one hand"


def test_vectors_stay_inside_the_scale() -> None:
    for card in cfg.CARDS.values():
        assert len(card["vector"]) == 3
        assert all(0 <= value <= 3 for value in card["vector"]), card["name"]


def test_price_is_not_a_power_ranking() -> None:
    """Every deck must hold a pair where the dearer card is no stronger on the
    round's target, or players would just buy the most expensive thing they can
    afford and the budget would stop being a decision."""
    for index, spec in enumerate(cfg.ROUNDS):
        target_index = cfg.DOMAINS.index(spec["target"])
        for role in ("attacker", "defender"):
            deck = cfg.cards_for(index, role)
            inversion = any(
                cfg.CARDS[a]["cost"] > cfg.CARDS[b]["cost"]
                and cfg.CARDS[a]["vector"][target_index] <= cfg.CARDS[b]["vector"][target_index]
                for a in deck
                for b in deck
            )
            assert inversion, f"round {spec['round']} {role} has no price inversion"


# --- scoring ------------------------------------------------------------------
def test_scoring_rows_always_sum_to_the_same_pool() -> None:
    for attack_points, defence_points in cfg.POINTS.values():
        assert attack_points + defence_points == 600


def test_a_breakthrough_always_scores_more_than_a_held_round() -> None:
    """The side that wins the round must also score more, or "won the round but
    lost the points" reads as a broken rule."""
    worst_break = min(points[0] for broke, flanks in cfg.POINTS if broke for points in [cfg.POINTS[(broke, flanks)]])
    best_hold = max(cfg.POINTS[(False, flanks)][0] for flanks in (0, 1, 2))
    assert worst_break > best_hold


def test_the_design_review_example() -> None:
    """Attacker D5 P2 I6 against defence D4 P5 I6.

    Same two builds, two rounds, two different stories - which is the whole
    point of binding the target to the scenario.
    """
    attack = (5, 2, 6)
    defence = (4, 5, 6)

    on_data = cfg.score_round(attack, defence, cfg.DATA)
    assert on_data["broke_target"] is True
    assert on_data["flanks"] == 0
    assert (on_data["attack_points"], on_data["defence_points"]) == (320, 280)

    on_people = cfg.score_round(attack, defence, cfg.PEOPLE)
    assert on_people["broke_target"] is False
    assert on_people["flanks"] == 1
    assert (on_people["attack_points"], on_people["defence_points"]) == (120, 480)


def test_a_tie_holds_the_line() -> None:
    """The defender does not have to outspend the attack, only match it."""
    result = cfg.score_round((6, 0, 0), (6, 0, 0), cfg.DATA)
    assert result["broke_target"] is False
    assert result["dims"][0]["result"] == "tie"


def test_every_flank_counts() -> None:
    result = cfg.score_round((9, 9, 9), (0, 0, 0), cfg.DATA)
    assert result["flanks"] == 2
    assert result["outcome"] == "Total compromise"
    assert (result["attack_points"], result["defence_points"]) == (600, 0)


# --- voting aggregation -------------------------------------------------------
def test_the_most_voted_cards_are_bought() -> None:
    deck = cfg.cards_for(0, "attacker")
    chosen, dropped = aggregate(
        {1: ["fake_sign_in_page", "urgent_deadline"], 2: ["fake_sign_in_page"]},
        deck,
        cfg.DATA,
        cfg.TOTAL_BUDGET,
    )
    assert "fake_sign_in_page" in chosen
    assert dropped == []


def test_a_tie_is_broken_by_target_strength_before_price() -> None:
    """Price is the last thing consulted, not the first.

    Sorting by price would reward voting for expensive cards and drain the
    shared pool before the target is covered. Here the dearer card wins because
    it is the one that scores on the round's target.
    """
    deck = cfg.cards_for(1, "attacker")
    chosen, _ = aggregate(
        {1: ["lookalike_website"], 2: ["do_not_escalate"]},
        deck,
        cfg.PEOPLE,
        cfg.TOTAL_BUDGET,
    )
    assert cfg.CARDS["do_not_escalate"]["cost"] > cfg.CARDS["lookalike_website"]["cost"]
    assert chosen[0] == "do_not_escalate"


def test_a_card_the_team_cannot_afford_is_skipped() -> None:
    deck = cfg.cards_for(0, "attacker")
    # Round one prices run 8k, 13k, 4k, 2k, 1k. With $15,000 the two strongest
    # do not fit together, so the dearer attachment is logged as unaffordable
    # and a cheaper card takes the slot instead of the vote being wasted.
    chosen, dropped = aggregate(
        {1: ["fake_sign_in_page", "malicious_attachment", "fake_sender_name"]},
        deck,
        cfg.DATA,
        15_000,
    )
    assert "malicious_attachment" not in chosen
    assert cfg.cost_of(chosen) <= 15_000
    assert len(chosen) <= cfg.MAX_CARDS
    assert [entry["reason"] for entry in dropped] == ["budget"]


def test_the_four_card_limit_is_respected() -> None:
    deck = cfg.cards_for(1, "attacker")
    chosen, _ = aggregate(
        {1: ["urgent_wire", "do_not_escalate", "lookalike_website", "vishing_call"]},
        deck,
        cfg.PEOPLE,
        cfg.TOTAL_BUDGET,
    )
    assert len(chosen) <= cfg.MAX_CARDS


def test_a_silent_team_buys_nothing() -> None:
    """No votes is no build.

    Filling in the best affordable hand for a quiet team let a side that never
    touched its iPad beat the side that voted - the exact complaint that came
    back from the first play test.
    """
    deck = cfg.cards_for(0, "attacker")
    chosen, log = aggregate({}, deck, cfg.DATA, cfg.TOTAL_BUDGET)
    assert chosen == []
    assert log == []


def test_a_round_where_nobody_voted_is_void() -> None:
    """Both sides silent is a wash, not a 600-point gift to the defender."""
    engine = make_engine()
    engine.start()
    engine.advance()
    engine.advance()
    engine.advance()
    result = engine.last_result
    assert result is not None
    assert result["void"] is True
    assert result["outcome"] == "No bids"
    assert result["winner"] is None
    assert engine.scores == {"red": 0, "blue": 0}
    assert engine.rounds_won == {"red": 0, "blue": 0}


def test_a_side_that_did_not_vote_forfeits_the_round() -> None:
    engine = make_engine()
    engine.start()
    engine.advance()
    engine.advance()
    # Only Blue votes; Red stays silent and must lose the round it attacks.
    for player_id in engine.teams["blue"]:
        engine.cast_vote(player_id, ["block_spam", "security_training"])
    engine.advance()
    result = engine.last_result
    assert result is not None
    assert engine.picks["red"] == []
    assert engine.no_bid["red"] is True
    assert result["winner"] == "blue"


# --- votes --------------------------------------------------------------------
def test_a_card_outside_the_rounds_deck_is_refused() -> None:
    engine = make_engine()
    engine.start()
    engine.advance()  # team_reveal -> brief
    engine.advance()  # brief -> select
    with pytest.raises(SabotageError):
        engine.cast_vote(1, ["poison_source"])


def test_a_hand_beyond_what_is_left_is_refused() -> None:
    """Money belongs to the match, not to the round: by round four the same
    deck can hold a hand the team can no longer pay for."""
    engine = make_engine()
    engine.start()
    engine.advance()
    engine.advance()
    for _ in range(len(cfg.ROUNDS) - 1):
        play_round(engine)
        engine.advance()  # select -> reveal
        engine.advance()  # reveal -> next select

    assert engine.round == len(cfg.ROUNDS) - 1
    dearest = sorted(engine.deck_for("red"), key=lambda card: -cfg.CARDS[card]["cost"])
    hand = dearest[: cfg.MAX_CARDS]
    assert cfg.cost_of(hand) > engine.remaining("red")
    with pytest.raises(SabotageError):
        engine.cast_vote(1, hand)


def test_more_than_four_cards_is_refused() -> None:
    engine = make_engine()
    engine.start()
    engine.advance()
    engine.advance()
    with pytest.raises(SabotageError):
        engine.cast_vote(
            1,
            ["urgent_deadline", "personal_details", "fake_sender_name", "fake_sign_in_page", "malicious_attachment"],
        )


def test_votes_are_only_open_while_selecting() -> None:
    engine = make_engine()
    engine.start()
    assert engine.phase == "team_reveal"
    with pytest.raises(SabotageError):
        engine.cast_vote(1, ["fake_sign_in_page"])


# --- the show -----------------------------------------------------------------
def test_a_full_show_runs_four_rounds_and_finishes() -> None:
    engine = make_engine()
    engine.start()
    assert engine.phase == "team_reveal"

    engine.advance()
    assert engine.phase == "brief"
    engine.advance()

    for round_index in range(len(cfg.ROUNDS)):
        assert engine.phase == "select", f"round {round_index + 1}"
        assert engine.round == round_index
        play_round(engine)
        engine.advance()  # select -> reveal
        assert engine.phase == "reveal"
        assert engine.last_result is not None
        assert len(engine.history) == round_index + 1
        engine.advance()

    assert engine.phase == "final"
    assert engine.round == len(cfg.ROUNDS) - 1
    # Four rounds of a 600-point pool: nothing is created or destroyed.
    assert engine.scores["red"] + engine.scores["blue"] == 600 * len(cfg.ROUNDS)
    assert engine.rounds_won["red"] + engine.rounds_won["blue"] == len(cfg.ROUNDS)
    # Nobody finished the game with money to spare: the pool is the constraint.
    assert engine.remaining("red") == cfg.TOTAL_BUDGET - engine.spent("red")
    assert engine.snapshot()["total_budget"] == cfg.TOTAL_BUDGET

    engine.advance()
    assert engine.phase == "done"


def test_the_first_round_gets_the_longest_clock() -> None:
    """Round one is also the tutorial, so it gets more time than the rest."""
    assert cfg.TIMINGS["select_first"] > cfg.TIMINGS["select"]


def test_no_build_is_public_while_selecting() -> None:
    """Both sides commit at the same time. If the broadcast carried a live
    build, the other team would read it and the round would become a copy of
    whoever moved last."""
    engine = make_engine()
    engine.start()
    engine.advance()
    engine.advance()
    engine.cast_vote(1, ["fake_sign_in_page"])
    snapshot = engine.snapshot()
    assert snapshot["phase"] == "select"
    assert snapshot["target"] == cfg.DATA
    assert snapshot["builds"] == {}
    assert snapshot["vectors"] == {}
    assert snapshot["votes"] == {}


def test_a_team_sees_its_own_build_only() -> None:
    engine = make_engine()
    engine.start()
    engine.advance()
    engine.advance()
    engine.cast_vote(1, ["fake_sign_in_page"])
    red = engine.team_view("red")
    blue = engine.team_view("blue")
    assert red["build"] == ["fake_sign_in_page"]
    assert red["round_budget"] == cfg.TOTAL_BUDGET
    assert red["budget"] == cfg.TOTAL_BUDGET - cfg.CARDS["fake_sign_in_page"]["cost"]
    assert red["role"] == "attacker"
    assert blue["build"] == []
    assert blue["role"] == "defender"


def test_the_snapshot_carries_the_round_table() -> None:
    engine = make_engine()
    engine.start()
    snapshot = engine.snapshot()
    assert snapshot["rounds"] == len(cfg.ROUNDS)
    assert snapshot["target"] == cfg.ROUNDS[0]["target"]
    assert snapshot["decks"]["red"] == cfg.cards_for(0, "attacker")
    assert snapshot["decks"]["blue"] == cfg.cards_for(0, "defender")


# --- pause / resume -----------------------------------------------------------
def test_pause_freezes_the_clock_and_resume_restores_it() -> None:
    engine = make_engine()
    engine.start()

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
    engine.advance()
    engine.advance()
    engine.pause()
    with pytest.raises(SabotageError):
        engine.cast_vote(1, ["fake_sign_in_page"])


# --- API shell ----------------------------------------------------------------
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


async def test_start_opens_with_the_team_draw(client: AsyncClient, admin_headers: dict) -> None:
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
        "/api/sabotage/sab-start/vote", json={"cards": ["fake_sign_in_page"]}, headers=player
    )
    assert voted.status_code == 409
    sabotages.remove("sab-start")


async def test_vote_accepts_a_hand_from_the_players_own_deck(
    client: AsyncClient, admin_headers: dict
) -> None:
    await client.post("/api/rooms", json={"code": "sab-vote"}, headers=admin_headers)
    players: dict[str, tuple[dict[str, str], int]] = {}
    for name in ("One", "Two", "Three", "Four"):
        players[name] = await _login(client, name)
        await client.post("/api/rooms/sab-vote/join", headers=players[name][0])
    await client.post("/api/rooms/sab-vote/teams/assign", headers=admin_headers)
    await client.post("/api/sabotage/sab-vote/start", headers=admin_headers)

    engine = sabotages.get("sab-vote")
    assert engine is not None
    engine.advance()  # team_reveal -> brief
    engine.advance()  # brief -> select

    red_name = next(name for name, (_, pid) in players.items() if pid in engine.teams["red"])
    voted = await client.post(
        "/api/sabotage/sab-vote/vote",
        json={"cards": ["fake_sign_in_page", "urgent_deadline"]},
        headers=players[red_name][0],
    )
    assert voted.status_code == 200
    player_id = players[red_name][1]
    assert engine.votes[player_id] == ["fake_sign_in_page", "urgent_deadline"]

    blue_name = next(name for name, (_, pid) in players.items() if pid in engine.teams["blue"])
    refused = await client.post(
        "/api/sabotage/sab-vote/vote",
        json={"cards": ["poison_source"]},
        headers=players[blue_name][0],
    )
    assert refused.status_code == 409
    sabotages.remove("sab-vote")


async def test_vote_requires_a_player_token(client: AsyncClient, admin_headers: dict) -> None:
    await client.post("/api/rooms", json={"code": "sab-auth"}, headers=admin_headers)
    await client.post("/api/sabotage/sab-auth/start", headers=admin_headers)
    response = await client.post("/api/sabotage/sab-auth/vote", json={"cards": []})
    assert response.status_code == 401
    sabotages.remove("sab-auth")


async def test_a_paused_show_still_starts_the_game(
    client: AsyncClient, admin_headers: dict
) -> None:
    """Jumping to the node while paused used to drop the old engine and start
    nothing, so every screen kept showing the last result."""
    from app.rooms import rooms

    await client.post("/api/rooms", json={"code": "sab-paused-go"}, headers=admin_headers)
    for name in ("One", "Two"):
        headers, _ = await _login(client, name)
        await client.post("/api/rooms/sab-paused-go/join", headers=headers)

    engine = rooms.get("sab-paused-go")
    assert engine is not None
    await engine.go_to(SABOTAGE_INDEX)
    assert sabotages.get("sab-paused-go") is not None

    await client.post("/api/rooms/sab-paused-go/pause", headers=admin_headers)
    await client.post(f"/api/rooms/sab-paused-go/go/{SABOTAGE_INDEX}", headers=admin_headers)

    restarted = sabotages.get("sab-paused-go")
    assert restarted is not None, "a paused show must still start the game"
    assert restarted.phase == "team_reveal"
    assert restarted.paused is True, "it must stay frozen while the show is paused"

    await engine.finish()
    sabotages.remove("sab-paused-go")


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


# --- auto-start and settlement ------------------------------------------------
async def test_auto_start_draws_the_teams_at_the_intro(
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

    engine = rooms.get("sab-auto")
    assert engine is not None
    await engine.go_to(SABOTAGE_INDEX)

    sab = sabotages.get("sab-auto")
    assert sab is not None
    # The draw is the first thing the players see: no earlier phase.
    assert sab.phase == "team_reveal"

    async with SessionLocal() as session:
        players = (
            await session.execute(select(Player).where(Player.room_code == "sab-auto"))
        ).scalars().all()
        teams = {player.team for player in players}
        assert teams == {"red", "blue"}

    await engine.finish()
    sabotages.remove("sab-auto")


async def test_every_jump_back_to_the_node_is_a_new_draw(
    client: AsyncClient, admin_headers: dict
) -> None:
    from app.rooms import rooms

    await client.post("/api/rooms", json={"code": "sab-rejump"}, headers=admin_headers)
    for name in ("One", "Two"):
        headers, _ = await _login(client, name)
        await client.post("/api/rooms/sab-rejump/join", headers=headers)

    engine = rooms.get("sab-rejump")
    assert engine is not None

    await client.post(f"/api/rooms/sab-rejump/go/{SABOTAGE_INDEX}", headers=admin_headers)
    first = sabotages.get("sab-rejump")
    assert first is not None
    assert first.phase == "team_reveal"

    await client.post("/api/rooms/sab-rejump/go/0", headers=admin_headers)
    assert sabotages.get("sab-rejump") is None

    await client.post(f"/api/rooms/sab-rejump/go/{SABOTAGE_INDEX}", headers=admin_headers)
    second = sabotages.get("sab-rejump")
    assert second is not None
    assert second is not first
    assert second.phase == "team_reveal"

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

    assert sabotages.get("sab-solo") is None
    assert engine.index == SABOTAGE_INDEX + 1
    assert (engine._node() or {}).get("game") != "sabotage"

    async with SessionLocal() as session:
        score = await session.get(Score, player_id)
        assert score is not None
        assert score.sabotage_score == cfg.SOLO_SCORE
        player = await session.get(Player, player_id)
        assert player is not None
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


def test_the_flow_slot_is_long_enough_for_the_game() -> None:
    """The engine runs its own clock; this node only guards the slot."""
    from app.flow import ROUND_FLOW

    node = next(node for node in ROUND_FLOW if node.get("game") == "sabotage")
    game_total = (
        cfg.TIMINGS["team_reveal"]
        + cfg.TIMINGS["brief"]
        + cfg.TIMINGS["select_first"]
        + cfg.TIMINGS["select"] * (len(cfg.ROUNDS) - 1)
        + cfg.TIMINGS["reveal"] * len(cfg.ROUNDS)
        + cfg.TIMINGS["final"]
    )
    assert node["duration_sec"] >= game_total
