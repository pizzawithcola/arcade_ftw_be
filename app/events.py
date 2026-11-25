"""The events a player can sign in for.

One list, kept here rather than in the frontend, because it is the thing the
login page renders *and* the thing the login endpoint validates against.
Keeping two copies would let the dropdown offer a value the backend rejects -
or, worse, quietly stop offering one it still accepts.

A new event is therefore a backend-only change: add it here and the dropdown
picks it up on the next page load.
"""

from __future__ import annotations

# `tab` is what the report's tab says - short enough to fit five across a
# console - while `label` is what the report itself is headed with. They are
# different on purpose: a tab is a place to click, a title is a name.
#
# `dates` and `facts` are what the organisers said the show was, not something
# derived from the rows: sign-ups straggle in around the edges (a test account on
# the 11th, a rehearsal on the 4th), so a min/max over `players.created_at` would
# print a run of dates that does not match the event anyone attended.
EVENTS: list[dict[str, str]] = [
    {
        "key": "hk_fintech_week",
        "label": "Hong Kong Fintech Week",
        "short": "HK Fintech",
        "tab": "HK FTW",
        "modules": ("hallucination", "data", "sabotage", "ultimate"),
        "dates": "Nov. 2-3",
        "start": "2026-11-02",
        "end": "2026-11-03",
        "facts": "1 booth · 3 hours · 4 games · 10 mins per round",
    },
    {
        "key": "hk_employee",
        "label": "Hong Kong Employee Week",
        "short": "HK Employee",
        "tab": "HK Employee",
        "modules": ("hallucination", "data", "sabotage", "ultimate"),
        "dates": "Nov. 9-11",
        "start": "2026-11-09",
        "end": "2026-11-11",
        "facts": "1 booth · 3 days · 4 games · 10 mins per round",
    },
    {
        "key": "sg_fintech_week",
        "label": "Singapore Fintech Festival",
        "short": "SG Fintech",
        "tab": "SG FTF",
        # Sabotage was switched off in Singapore, so a perfect run here is worth
        # 2,000 rather than 2,800. Any comparison between events that ignores
        # that is comparing a three-game night to a four-game one.
        "modules": ("hallucination", "data", "ultimate"),
        "dates": "Nov. 18-20",
        "start": "2026-11-18",
        "end": "2026-11-20",
        "facts": "1 booth · 3 days · 3 games · 5 mins per round",
    },
]

EVENT_KEYS: frozenset[str] = frozenset(event["key"] for event in EVENTS)
