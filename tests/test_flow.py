"""The rundown itself: every game is introduced, and the openings cost a slot."""

from app.flow import MODULES, ROUND_FLOW, build_flow

# (opening key, the node it introduces, the section it scores into)
# Sabotage is the exception: it draws its teams and then shows its own rules
# card inside the node, so a flow-level opening would introduce it twice.
EXPECTED_OPENINGS = [
    ("hallucination", "hallucination", "hallucination"),
    ("data_shadow", "data_shadow", "data"),
    ("ultimate_quiz", None, "ultimate"),
]

OPENING_SECONDS = 15
TITLE_SECONDS = 3
# The closing score has to be readable after the last verdict. One second of it
# is the same as none, so every question node keeps at least this much.
MIN_TAIL_SECONDS = 3


def test_every_game_has_an_opening_in_front_of_it():
    """An opening sits immediately before the node it introduces.

    The console relies on that adjacency: the opening is grouped into the same
    section as the game, and reads as the button to the left of `challenge`.
    """
    for key, game, section in EXPECTED_OPENINGS:
        at = next(
            (i for i, node in enumerate(ROUND_FLOW) if node.get("game") == key and node["type"] == "opening"),
            None,
        )
        assert at is not None, f"no opening for {key}"

        opening = ROUND_FLOW[at]
        assert opening["type"] == "opening"
        assert opening["duration_sec"] == OPENING_SECONDS
        assert opening["title_sec"] == TITLE_SECONDS
        assert opening["section"] == section
        assert opening.get("score") is None, "an opening must not score"

        after = ROUND_FLOW[at + 1]
        assert after.get("section", after.get("score")) == section
        if game is not None:
            assert after.get("game") == game, f"{key} does not introduce its game"
        else:
            # The ultimate round is a quiz, so its opening introduces that node.
            assert after["type"] == "quiz"


def test_opening_count_matches_the_games():
    keys = [node.get("game") for node in ROUND_FLOW if node["type"] == "opening"]
    assert keys == [key for key, _, _ in EXPECTED_OPENINGS]


def test_the_name_holds_inside_the_opening():
    """The 15s opening is 3s of name and 12s of rules, not two flow nodes.

    Splitting it in the flow cost four extra nodes for one beat the audience
    reads as a single card, so the split lives in `title_sec` instead - and it
    has to leave real time for the rules.
    """
    for node in ROUND_FLOW:
        if node["type"] != "opening":
            continue
        assert 0 < node["title_sec"] < node["duration_sec"]
        assert node["duration_sec"] - node["title_sec"] >= 10, "the rules need their twelve seconds"


def test_no_node_still_uses_the_old_split():
    """`title` and `brief` types are gone: one node, one type."""
    kinds = {node["type"] for node in ROUND_FLOW}
    assert "title" not in kinds
    assert "brief" not in kinds


def test_question_nodes_outlast_their_questions():
    """The node must be longer than its own content, with a tail for the score.

    Landing the last verdict on the node's final second means the final score is
    never on screen - the same class of bug as a node that ends mid-question.
    A one-second tail satisfies `>` while still being unreadable, so the tail has
    a floor.
    """
    for node in ROUND_FLOW:
        if not node.get("questions"):
            continue
        needed = (node.get("intro_sec") or 0) + node["questions"] * node["seconds_each"]
        tail = node["duration_sec"] - needed
        # A round may cut its closing score page on purpose - Hallucination ends
        # on its last verdict and hands straight to the leaderboard - and says so
        # with `closing_sec`. Every other round still needs a readable tail.
        wanted = node.get("closing_sec", MIN_TAIL_SECONDS)
        assert tail >= wanted, f"{node['title']} leaves {tail}s for the closing score"
        assert tail >= 0, f"{node['title']} ends mid-question"


def test_data_shadow_gives_the_post_fifty_seconds():
    """The post is judged a paragraph at a time, so reading it is the whole task.

    The opening node carries the same `game` key, so this has to match the node
    the engine actually runs.
    """
    node = next(
        node for node in ROUND_FLOW if node["type"] == "game" and node.get("game") == "data_shadow"
    )
    assert node["duration_sec"] >= 50
    # The answer is withheld until the clock runs out, so the reveal is the tail.
    assert node["duration_sec"] - 50 >= 10


def test_only_the_ultimate_round_still_has_a_quiz():
    """The quizzes after Hallucination and Data Shadow were cut, so a quiz node
    can only belong to the Ultimate section."""
    quizzes = [node for node in ROUND_FLOW if node["type"] == "quiz"]
    assert len(quizzes) == 1
    assert quizzes[0]["score"] == "ultimate"
    assert quizzes[0]["questions"] == 3


def test_no_quiz_opens_on_a_title_card():
    """The Ultimate quiz starts on its first question: no `intro_sec`, or the
    engine and the wall would spend the first five seconds on a card."""
    for node in ROUND_FLOW:
        if node["type"] == "quiz":
            assert not node.get("intro_sec"), f"{node['title']} still has an intro card"


def test_the_welcome_outlasts_its_own_reveal():
    """The intro lays out four cards; the slot must outlast the animation.

    The frontend releases card N at 1200 + 900*N ms, each lands in 520ms and
    sweeps its rule for another 880ms, and the footer arrives at 4200. Everything
    is settled by 4.8s, so a five-second slot is the floor and ten is what the
    flow uses - the remaining five seconds are for reading the four games.
    """
    intro = ROUND_FLOW[0]
    assert intro["type"] == "intro"
    assert intro["duration_sec"] >= 5


# ─── modules: what a room is built from ───────────────────────────────────────


def test_every_node_names_its_module():
    """A node without `requires` would appear in every room, hidden or not.

    Declaring the key on the node (instead of letting `build_flow` guess it from
    `game` / `section`) is only worth it while this test holds: a new node that
    forgets to name its module fails here rather than turning up in a show that
    asked for it to be left out.
    """
    for node in ROUND_FLOW:
        module = node.get("requires")
        assert module in MODULES, f"{node['title']} names module {module!r}"


def test_a_room_with_no_module_choice_gets_the_whole_show():
    """None means all seven: a room created before modules existed keeps playing
    the same thirteen nodes it always did."""
    assert build_flow() == ROUND_FLOW
    assert len(build_flow()) == 13


def test_hiding_a_game_takes_its_rules_and_its_board_with_it():
    """The three nodes of a game share one module, so it cannot leave an orphan.

    A board with no game in front of it is eight seconds of zeroes, so the two
    have to be filtered by the same key - that is the whole reason `requires`
    lives on the node rather than being derived twice.
    """
    without_battle = [module for module in MODULES if module != "sabotage"]
    flow = build_flow(without_battle)

    assert len(flow) == 11
    assert not any(node.get("game") == "sabotage" for node in flow)
    for node in flow:
        # `total` is the activity board: it ranks the whole show rather than one
        # game, so it is the one board that has no scoring node behind it.
        if node["type"] == "leaderboard" and node["section"] != "total":
            section = node["section"]
            assert any(
                other.get("score") == section for other in flow
            ), f"{node['title']} has no game behind it"


def test_the_show_keeps_its_order_whatever_is_hidden():
    """Filtering must never reorder: the rundown reads the same way minus what
    was left out, or the welcome map would promise a sequence the show does not
    run."""
    full = [node["title"] for node in build_flow()]
    kept = [node["title"] for node in build_flow(["welcome", "data", "total", "wrapup"])]
    positions = [full.index(title) for title in kept]
    assert positions == sorted(positions)


def test_each_build_hands_out_its_own_nodes():
    """`ROUND_FLOW` is module-level, so a build has to copy it: two rooms that
    shared a dict would see each other's edits."""
    first, second = build_flow(), build_flow()
    assert first[0] is not second[0]
    assert first[0] is not ROUND_FLOW[0]


def test_answered_nodes_publish_the_window_the_room_answers_in():
    """`answer_sec` is what the wall's clock counts.

    A node's clock is longer than the window the room can act in: five seconds
    of verdict follow each question, ten of reveal follow Data Shadow. The big
    screen published the whole node clock and so read "0:25" while every iPad
    read "0:20" - the one number in the room nobody could agree on. The split is
    therefore data, and every node whose clock the devices also show declares it.
    """
    # Sabotage is left out on purpose: its node clock is a slot, and both
    # screens read the battle's own clock from the same payload instead.
    timed = [
        node
        for node in ROUND_FLOW
        if node.get("seconds_each") or (node["type"] == "game" and node.get("game") == "data_shadow")
    ]
    assert len(timed) == 3
    for node in timed:
        cycle = node.get("seconds_each") or node["duration_sec"]
        assert "answer_sec" in node, f"{node['title']} hides its answer window"
        # The tail has to be a real stretch: it is the verdict, and an empty one
        # would mean the wall and the iPad were counting the same window twice.
        assert node["answer_sec"] < cycle, f"{node['title']} has no verdict left"
