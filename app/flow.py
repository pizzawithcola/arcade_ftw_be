"""Main-line flow config. Edit the list to change the show, not the engine."""

ROUND_FLOW: list[dict] = [
    {"type": "game", "game": "hallucinate", "duration_sec": 180},
    {"type": "quiz", "question_ids": []},
    {"type": "game", "game": "datashadows", "duration_sec": 240},
    {"type": "quiz", "question_ids": []},
]
