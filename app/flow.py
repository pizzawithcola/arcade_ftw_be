"""Main-line flow config. Edit the list to change the show, not the engine.

Durations are short on purpose while the slices are still placeholders: a full
run takes a couple of minutes, which makes auto-advance and broadcast easy to
watch. Every node carries a title the frontend can render without knowing the
node type.
"""

ROUND_FLOW: list[dict] = [
    {"type": "intro", "title": "Welcome", "duration_sec": 10},
    {"type": "game", "game": "hallucinate", "title": "Hallucinate", "duration_sec": 30},
    {"type": "quiz", "title": "Quiz round 1", "duration_sec": 20},
    {"type": "game", "game": "datashadows", "title": "Data Shadows", "duration_sec": 30},
    {"type": "quiz", "title": "Quiz round 2", "duration_sec": 20},
    {"type": "outro", "title": "That's all", "duration_sec": 10},
]
