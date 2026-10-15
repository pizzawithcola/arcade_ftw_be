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

Durations for the built screens are content-driven: each question gets 20s and
the node adds a few seconds of tail, so the engine never cuts a game off before
its final reveal. The screens that are still placeholders keep short values.
"""

# Intro - Hallucination - Quiz - Data Shadow - Quiz - Sabotage - Ultimate Quiz - Wrap up
ROUND_FLOW: list[dict] = [
    {"type": "intro", "title": "Welcome", "duration_sec": 10},
    {"type": "game", "game": "hallucination", "title": "Hallucination", "duration_sec": 66},
    {"type": "quiz", "title": "Quiz round 1", "duration_sec": 46},
    {"type": "game", "game": "data_shadow", "title": "Data Shadow", "duration_sec": 30},
    {"type": "quiz", "title": "Quiz round 2", "duration_sec": 46},
    {"type": "game", "game": "sabotage", "title": "Sabotage", "duration_sec": 30},
    {"type": "quiz", "title": "Ultimate Quiz", "duration_sec": 46},
    {"type": "outro", "title": "Wrap up", "duration_sec": 10},
]
