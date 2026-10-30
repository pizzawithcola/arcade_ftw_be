"""The rundown itself: every game is introduced, and the intro costs a slot."""

from app.flow import ROUND_FLOW

# (brief key, the game or quiz it introduces, the section it scores into)
# Sabotage is the exception: it draws its teams and then shows its own rules
# card inside the node, so a flow-level brief would introduce it twice.
EXPECTED_BRIEFS = [
    ("hallucination", "hallucination", "hallucination"),
    ("data_shadow", "data_shadow", "data"),
    ("ultimate_quiz", None, "ultimate"),
]

BRIEF_SECONDS = 15


def test_every_game_has_a_brief_in_front_of_it():
    """A brief card sits immediately before the node it introduces.

    The console relies on that adjacency: the card is grouped into the same
    section as the game, and reads as the button to the left of `challenge`.
    """
    for key, game, section in EXPECTED_BRIEFS:
        at = next(
            (i for i, node in enumerate(ROUND_FLOW) if node.get("brief") == key),
            None,
        )
        assert at is not None, f"no brief for {key}"

        brief = ROUND_FLOW[at]
        assert brief["type"] == "brief"
        assert brief["duration_sec"] == BRIEF_SECONDS
        assert brief["section"] == section
        assert brief.get("score") is None, "a brief must not score"

        after = ROUND_FLOW[at + 1]
        assert after.get("section", after.get("score")) == section
        if game is not None:
            assert after.get("game") == game, f"{key} does not introduce its game"
        else:
            # The ultimate round is a quiz, so its brief introduces that node.
            assert after["type"] == "quiz"


def test_brief_count_matches_the_games():
    kinds = [node.get("brief") for node in ROUND_FLOW if node["type"] == "brief"]
    assert kinds == [key for key, _, _ in EXPECTED_BRIEFS]


def test_question_nodes_outlast_their_questions():
    """The node must be longer than its own content, with a tail for the score.

    Landing the last verdict on the node's final second means the final score is
    never on screen - the same class of bug as a node that ends mid-question.
    """
    for node in ROUND_FLOW:
        if not node.get("questions"):
            continue
        needed = (node.get("intro_sec") or 0) + node["questions"] * node["seconds_each"]
        assert node["duration_sec"] > needed, f"{node['title']} ends with its last question"


def test_data_shadow_gives_the_post_eighty_seconds():
    """The round was too short to read a paragraph and sweep it for names."""
    node = next(node for node in ROUND_FLOW if node.get("game") == "data_shadow")
    assert node["duration_sec"] > 80


def test_every_game_opens_with_its_name():
    """A 5s title card sits in front of each game's rules card.

    The name lands before the rules on purpose: the room should know what it is
    about to play before it is told how. Sabotage has no rules card in the flow,
    so its title sits directly before the game node.
    """
    from app.flow import ROUND_FLOW as FLOW

    for key, game, _section in EXPECTED_BRIEFS:
        at = next(i for i, node in enumerate(FLOW) if node.get("brief") == key)
        title = FLOW[at - 1]
        assert title["type"] == "title", f"{key} has no title card before its rules"
        assert title["duration_sec"] == 5
        assert title.get("score") is None, "a title card must not score"
        # The brief is still immediately before the game, which the console's
        # grouping relies on.
        assert FLOW[at + 1].get("game") == game or FLOW[at + 1]["type"] == "quiz"

    # Sabotage: title, then the game itself.
    sabotage = next(i for i, node in enumerate(FLOW) if node.get("game") == "sabotage")
    assert FLOW[sabotage - 1]["type"] == "title"
    assert FLOW[sabotage - 1]["title"] == "sabotage"


def test_titles_open_a_section_and_score_nothing():
    titles = [node for node in ROUND_FLOW if node["type"] == "title"]
    assert len(titles) == 4
    for node in titles:
        assert node["section"], "a title has to sit in its game's section"
        assert node.get("score") is None


def test_the_welcome_outlasts_its_own_reveal():
    """The intro lays out four cards a second apart; the slot must outlast it.

    The frontend releases card N at 2200 + 1000*N ms and the footer at 6400, so
    anything under nine seconds would cut the welcome off mid-build.
    """
    intro = ROUND_FLOW[0]
    assert intro["type"] == "intro"
    assert intro["duration_sec"] >= 9
