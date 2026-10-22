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
questions, or the engine advances mid-question. Hallucination is two 30s
questions and each quiz is two 20s, plus a few seconds of tail for the final
reveal. The screens that are still placeholders keep short values.

``questions`` + ``seconds_each`` also let the engine say *which* question is on
screen right now (see ``Engine.snapshot``). The big screen needs that to show the
same question every iPad is answering; without it the question would only exist
inside each device.
"""

# Intro - Hallucination - Quiz - Data Shadow - Quiz - Sabotage - Ultimate Quiz - Wrap up
#
# Scoring nodes name the partition their points belong to (`score`). A game and
# the quiz that follows it share one partition, so the round is scored as one
# section rather than as five separate columns. The server maps this key to a
# `scores` column; clients never name the column themselves.
ROUND_FLOW: list[dict] = [
    {"type": "intro", "title": "Welcome", "duration_sec": 10},
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
        "duration_sec": 46,
        "questions": 2,
        "seconds_each": 20,
        "score": "hallucination",
    },
    {"type": "game", "game": "data_shadow", "title": "Data Shadow", "duration_sec": 51, "score": "data"},
    {
        "type": "quiz",
        "title": "Quiz round 2",
        "duration_sec": 46,
        "questions": 2,
        "seconds_each": 20,
        "score": "data",
    },
    {
        "type": "game",
        "game": "sabotage",
        "title": "Sabotage",
        # The Sabotage engine runs its own clock (~2.5 minutes plus tie-break
        # headroom) and broadcasts it over WS; this node only holds the slot
        # in the main flow for as long as the game needs.
        "duration_sec": 170,
        "score": "sabotage",
    },
    {
        "type": "quiz",
        "title": "Ultimate Quiz",
        "duration_sec": 46,
        "questions": 2,
        "seconds_each": 20,
        "score": "ultimate",
    },
    {"type": "outro", "title": "Wrap up", "duration_sec": 10},
]
