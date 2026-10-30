"""Main-line flow config. Edit the list to change the show, not the engine.

A node has two identifying fields:

* ``type`` -- the **category**, and the only thing the engine cares about. It
  picks the layout, so every node in a category is staged the same way.
* ``game`` -- the **specific game**, present only on ``game`` nodes. Two games
  can look nothing alike while sharing the category, which is why the frontend
  dispatches on ``game`` for its own screen while ``type`` stays broad.

``title`` is display-only: the audience reads it, but no code branches on it.
That is what lets the two ordinary quiz rounds and the ultimate quiz all be
``type: "quiz"`` without the code having to tell them apart.

Durations for the built screens are content-driven: the node has to outlast its
questions, or the engine advances mid-question. A question is a fixed cycle:
25s to answer plus 5s reading the verdict for Hallucination, 20s plus 5s for a
quiz, and the verdict page advances itself so nobody has to press anything.
Quiz nodes also open with a 5s ``intro_sec`` title card, so ``duration_sec`` is
the intro plus both cycles. The screens that are still placeholders keep short
values.

``intro_sec`` is subtracted before ``seconds_each`` is applied, so the question
index the big screen shows is still derived from the node clock alone.

``questions`` + ``seconds_each`` also let the engine say *which* question is on
screen right now (see ``Engine.snapshot``). The big screen needs that to show the
same question every iPad is answering; without it the question would only exist
inside each device.

``title`` nodes are the 5s name card in front of each game, and ``brief`` nodes
are the 15s "how to play" card that follows it. ``title`` and ``brief`` each name
which game's wording to show; the engine treats both as ordinary timed nodes, so
nothing in the engine knows about intros. ``section`` is there so the console
groups the cards with the section they introduce.

The order is deliberate: the name lands first, so the room knows what it is about
to play, then the rules, then the game. A ``brief`` still sits immediately before
the node it introduces, which is what the console's grouping relies on.
"""

# Intro - [title, brief] Hallucination - Quiz - [title, brief] Data Shadow - Quiz
# - [title] Sabotage - [title, brief] Ultimate Quiz - Wrap up
#
# Four of those nodes are ``brief`` cards: 15 seconds of rules in front of each
# game, so nobody has to be told what to press on stage.
#
# Scoring nodes name the partition their points belong to (`score`). A game and
# the quiz that follows it share one partition, so the round is scored as one
# section rather than as five separate columns. The server maps this key to a
# `scores` column; clients never name the column themselves.
ROUND_FLOW: list[dict] = [
    # The welcome lays out the four games one card at a time, so the slot has to
    # outlast the reveal (2.2s lead-in + 4 cards a second apart + reading time).
    {"type": "intro", "title": "Welcome", "duration_sec": 12},
    {"type": "title", "title": "hallucination", "duration_sec": 5, "section": "hallucination"},
    {
        "type": "brief",
        "brief": "hallucination",
        "title": "How Hallucination works",
        "duration_sec": 15,
        "section": "hallucination",
    },
    {
        "type": "game",
        "game": "hallucination",
        "title": "Hallucination",
        "duration_sec": 66,
        "questions": 2,
        "seconds_each": 30,
        "score": "hallucination",
    },
    {
        "type": "quiz",
        "title": "Quiz round 1",
        # 5s title card + 2 x (20s answer + 5s verdict) + 5s of final score.
        "duration_sec": 60,
        "intro_sec": 5,
        "questions": 2,
        "seconds_each": 25,
        "score": "hallucination",
    },
    {
        "type": "leaderboard",
        "section": "hallucination",
        "title": "Hallucination standings",
        # Long enough for the count-up and the reorder to be read.
        "duration_sec": 8,
    },
    {"type": "title", "title": "data_shadow", "duration_sec": 5, "section": "data"},
    {
        "type": "brief",
        "brief": "data_shadow",
        "title": "How Data Shadow works",
        "duration_sec": 15,
        "section": "data",
    },
    {"type": "game", "game": "data_shadow", "title": "Data Shadow", "duration_sec": 86, "score": "data"},
    {
        "type": "quiz",
        "title": "Quiz round 2",
        # 5s title card + 2 x (20s answer + 5s verdict) + 5s of final score.
        "duration_sec": 60,
        "intro_sec": 5,
        "questions": 2,
        "seconds_each": 25,
        "score": "data",
    },
    {"type": "leaderboard", "section": "data", "title": "Data Shadow standings", "duration_sec": 8},
    {"type": "title", "title": "sabotage", "duration_sec": 5, "section": "sabotage"},
    # No `brief` node here on purpose: Sabotage draws its teams and then shows
    # its own 20-second rules card inside the node, so a flow-level card would
    # introduce the game before the game introduces itself.
    {
        "type": "game",
        "game": "sabotage",
        "title": "Sabotage",
        # The Sabotage engine runs its own clock (~2.5 minutes plus tie-break
        # headroom) and broadcasts it over WS; this node only holds the slot
        # in the main flow for as long as the game needs.
        # Sabotage v2 runs its own ~3.5 minute clock (8s draw + 20s rules +
        # 4 x (select + reveal) + final); this slot only has to be long enough.
        "duration_sec": 225,
        "score": "sabotage",
    },
    {
        "type": "leaderboard",
        "section": "sabotage",
        "title": "Sabotage standings",
        "duration_sec": 8,
    },
    {"type": "title", "title": "ultimate", "duration_sec": 5, "section": "ultimate"},
    {
        "type": "brief",
        "brief": "ultimate_quiz",
        "title": "How the Ultimate Quiz works",
        "duration_sec": 15,
        "section": "ultimate",
    },
    {
        "type": "quiz",
        "title": "Ultimate Quiz",
        # 5s title card + 2 x (20s answer + 5s verdict) + 5s of final score.
        "duration_sec": 60,
        "intro_sec": 5,
        "questions": 2,
        "seconds_each": 25,
        "score": "ultimate",
    },
    {
        "type": "leaderboard",
        "section": "ultimate",
        "title": "Ultimate standings",
        "duration_sec": 8,
    },
    # The final, activity-wide ranking. A placeholder until its design lands.
    {"type": "leaderboard", "section": "total", "title": "Activity Leaderboard", "duration_sec": 5},
    {"type": "outro", "title": "Wrap up", "duration_sec": 10},
]
