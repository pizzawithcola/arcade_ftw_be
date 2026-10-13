"""The events a player can sign in for.

One list, kept here rather than in the frontend, because it is the thing the
login page renders *and* the thing the login endpoint validates against.
Keeping two copies would let the dropdown offer a value the backend rejects -
or, worse, quietly stop offering one it still accepts.

A new event is therefore a backend-only change: add it here and the dropdown
picks it up on the next page load.
"""

from __future__ import annotations

EVENTS: list[dict[str, str]] = [
    {"key": "hk_fintech_week", "label": "Hong Kong Fintech Week"},
    {"key": "hk_employee", "label": "Hong Kong Employee"},
    {"key": "sg_fintech_week", "label": "Singapore Fintech Week"},
    {"key": "sg_employee", "label": "Singapore Employee"},
]

EVENT_KEYS: frozenset[str] = frozenset(event["key"] for event in EVENTS)
