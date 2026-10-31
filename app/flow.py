"""Main-line flow config. Edit the list to change the show, not the engine.

A node has two identifying fields:

* ``type`` -- the **category**, and the only thing the engine cares about. It
  picks the layout, so every node in a category is staged the same way.
* ``game`` -- the **specific game**, present only on ``game`` and ``opening``
  nodes. Two games can look nothing alike while sharing the category, which is
  why the frontend dispatches on ``game`` for its own screen while ``type``
  stays broad.

``title`` is display-only: the audience reads it, but no code branches on it.

Durations for the built screens are content-driven: the node has to outlast its
questions, or the engine advances mid-question. A question is a fixed cycle:
20s to answer plus 5s reading the verdict for both Hallucination and the quiz,
and the verdict page advances itself so nobody has to press anything. Every node
also keeps a tail after its last question so the closing score is not cut off -
``duration_sec`` is the cycles plus that tail.

``intro_sec`` is subtracted before ``seconds_each`` is applied, so the question
index the big screen shows is still derived from the node clock alone. No node
sets it today: both quiz screens start on their first question.

``questions`` + ``seconds_each`` also let the engine say *which* question is on
screen right now (see ``Engine.snapshot``). The big screen needs that to show the
same question every iPad is answering; without it the question would only exist
inside each device.

One ``opening`` node replaces the old pair of a 5s ``title`` card and a 15s
``brief`` card. It is still fifteen seconds, split by ``title_sec``: three of
name, twelve of rules, handed over inside the node so the flow has one node for
what the audience reads as one beat. ``title_sec`` is data rather than a frontend
constant so the split can move without rebuilding anything.

Sabotage has no ``opening`` node: it draws its teams first and then shows its own
rules card inside the game, so a flow-level opening in front of it would
introduce the game before the game introduces itself.

The order is deliberate: the name lands first, so the room knows what it is about
to play, then the rules, then the game.
"""

# Intro - [opening] Hallucination - [opening] Data Shadow - Sabotage
# - [opening] Ultimate Quiz - Total - Wrap up
#
# Three of those are ``opening`` nodes: three seconds of name, twelve of rules.
#
# Scoring nodes name the partition their points belong to (`score`). One column
# per section, so the board reads Hallucination / Data / Sabotage / Ultimate
# rather than one column per node. The server maps this key to a `scores`
# column; clients never name the column themselves.
ROUND_FLOW: list[dict] = [
    # The welcome lays out the four games one card at a time. The reveal is done
    # by the fifth second (2.2s of cards plus their entrances), so the rest of the
    # ten is reading time.
    {"type": "intro", "title": "Welcome", "duration_sec": 10},
    {
        "type": "opening",
        "game": "hallucination",
        "title": "AI Hallucination",
        "duration_sec": 15,
        "title_sec": 3,
        "section": "hallucination",
    },
    {
        "type": "game",
        "game": "hallucination",
        "title": "Hallucination",
        # 3 x (20s answer + 5s verdict) + 3s of closing score.
        "duration_sec": 78,
        "questions": 3,
        "seconds_each": 25,
        "score": "hallucination",
    },
    {
        "type": "leaderboard",
        "section": "hallucination",
        "title": "Hallucination standings",
        "duration_sec": 5,
    },
    {
        "type": "opening",
        "game": "data_shadow",
        "title": "Data Shadow",
        "duration_sec": 15,
        "title_sec": 3,
        "section": "data",
    },
    {
        "type": "game",
        "game": "data_shadow",
        "title": "Data Shadow",
        # 80s of reading and dragging, plus 10s that reveal what leaked - the
        # answer is withheld until the clock runs out, so the tail is the game.
        "duration_sec": 90,
        "score": "data",
    },
    {"type": "leaderboard", "section": "data", "title": "Data Shadow standings", "duration_sec": 5},
    # No `opening`: Sabotage draws its teams and then shows its own rules card
    # inside the node, so a flow-level card would introduce the game twice.
    {
        "type": "game",
        "game": "sabotage",
        "title": "Sabotage",
        # The Sabotage engine runs its own clock (see sabotage_config.TIMINGS:
        # 10s draw + 15s rules + 35s first pick + 3 x 25s picks + 4 x 5s reveals
        # + 5s final = 160s); this slot only has to be long enough, and 3s more
        # so a slow team draw cannot push the last reveal past the cut.
        "duration_sec": 163,
        "score": "sabotage",
    },
    {
        "type": "leaderboard",
        "section": "sabotage",
        "title": "Sabotage standings",
        "duration_sec": 5,
    },
    # No name card is needed separately: the opening carries it. There is also no
    # section leaderboard after the quiz - its scores land on the activity board,
    # which is the next node, so a section board would show the same numbers.
    {
        "type": "opening",
        "game": "ultimate_quiz",
        "title": "Ultimate Showdown",
        "duration_sec": 15,
        "title_sec": 3,
        "section": "ultimate",
    },
    {
        "type": "quiz",
        "title": "Ultimate Quiz",
        # 5 x (20s answer + 5s verdict) + 3s of closing score. No `intro_sec`:
        # the first question is on screen from the first second. The five are
        # drawn on the device from a seed, so the count is fixed and only the
        # questions vary - the node length never depends on the draw.
        "duration_sec": 128,
        "questions": 5,
        "seconds_each": 25,
        "score": "ultimate",
    },
    {
        "type": "leaderboard",
        "section": "total",
        "title": "Activity Leaderboard",
        "duration_sec": 10,
    },
    {"type": "outro", "title": "Wrap up", "duration_sec": 10},
]
