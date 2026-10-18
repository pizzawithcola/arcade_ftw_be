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
ROUND_FLOW: list[dict] = [
    {"type": "intro", "title": "Welcome", "duration_sec": 10},
    {
        "type": "game",
        "game": "hallucination",
        "title": "Hallucination",
        "duration_sec": 66,
        "questions": 2,
        "seconds_each": 30,
    },
    {"type": "quiz", "title": "Quiz round 1", "duration_sec": 46, "questions": 2, "seconds_each": 20},
    {"type": "game", "game": "data_shadow", "title": "Data Shadow", "duration_sec": 51},
    {"type": "quiz", "title": "Quiz round 2", "duration_sec": 46, "questions": 2, "seconds_each": 20},
    {"type": "game", "game": "sabotage", "title": "Sabotage", "duration_sec": 30},
    {"type": "quiz", "title": "Ultimate Quiz", "duration_sec": 46, "questions": 2, "seconds_each": 20},
    {"type": "outro", "title": "Wrap up", "duration_sec": 10},
]
