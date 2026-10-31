"""The rundown itself: every game is introduced, and the openings cost a slot."""

from app.flow import ROUND_FLOW

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
        assert tail >= MIN_TAIL_SECONDS, f"{node['title']} leaves {tail}s for the closing score"


def test_data_shadow_gives_the_post_eighty_seconds():
    """The round was too short to read a paragraph and sweep it for names.

    The opening node carries the same `game` key, so this has to match the node
    the engine actually runs.
    """
    node = next(
        node for node in ROUND_FLOW if node["type"] == "game" and node.get("game") == "data_shadow"
    )
    assert node["duration_sec"] >= 80
    # The answer is withheld until the clock runs out, so the reveal is the tail.
    assert node["duration_sec"] - 80 >= 10


def test_only_the_ultimate_round_still_has_a_quiz():
    """The quizzes after Hallucination and Data Shadow were cut, so a quiz node
    can only belong to the Ultimate section."""
    quizzes = [node for node in ROUND_FLOW if node["type"] == "quiz"]
    assert len(quizzes) == 1
    assert quizzes[0]["score"] == "ultimate"
    assert quizzes[0]["questions"] == 5


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
