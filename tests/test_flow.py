"""The rundown itself: every game is introduced, and the intro costs a slot."""

from app.flow import ROUND_FLOW

# (brief key, the game or quiz it introduces, the section it scores into)
EXPECTED_BRIEFS = [
    ("hallucination", "hallucination", "hallucination"),
    ("data_shadow", "data_shadow", "data"),
    ("sabotage", "sabotage", "sabotage"),
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
