"""Events from Arcade 1.0, kept as read-only archives.

UAE AI Connect ran on the previous system (`cyber_ai_festival_be`), which scored
five games out of 100 each and totalled about 500 with decimals. This system
scores four sections worth 600/800/800/600. Those numbers cannot be added,
averaged or ranked together without inventing a conversion, so an archived event
is **never** folded into the all-time summary or compared against a 2.0 event:
it is shown on its own page, with its own scale, and nothing else reads it.

The data is the response from that system's own read-only report endpoint,
vendored verbatim in `data/legacy_uae_ai_connect.json`. Nothing here talks to the
old system: it is finished, its event is closed, and switching its current event
to pull per-player rows would mutate production for a report.

What the old export does not contain is the per-player score list - only the top
ten and the per-game averages. That limit is carried through rather than papered
over: `leaderboard` here is a top ten, not a full board.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data"

# Labels the five 1.0 games. `game5` is the old system's own name for the round
# this system calls Ultimate Showdown, and it is the name the room uses now - the
# two are the same game, so the report says one thing throughout. The other four
# keep their 1.0 names because RetailDemolition and Phishing have no 2.0
# equivalent, which is the whole reason these numbers stay on their own page.
LEGACY_GAMES: list[tuple[str, str]] = [
    ("game1", "Hallucinate"),
    ("game2", "Data Shadows"),
    ("game3", "Retail Demolition"),
    ("game4", "Phishing"),
    ("game5", "Ultimate Showdown"),
]

# key -> (label, source file) for every archived event. Keys are distinct from
# `app.events.EVENT_KEYS` on purpose: nothing can sign in for an archived event.
ARCHIVES: dict[str, tuple[str, str]] = {
    "uae_ai_connect": ("Arcade 1.0 - UAE AI Connect", "legacy_uae_ai_connect.json"),
}

# Event facts the export cannot answer, so they are written down here rather than
# guessed from it: how many booths, days, games and minutes per round the show
# ran, as counted by the organisers. `showdown.rooms` (29) is game rooms, not
# venue booths - deriving any of these from the JSON produces a different, wrong
# number.
ARCHIVE_FACTS: dict[str, dict[str, int]] = {
    "uae_ai_connect": {"rooms": 2, "days": 2, "games": 5, "mins": 30},
}

# Arcade 1.0 scored each game out of 100, so a perfect run of the five was 500 -
# the number the archive page prints beside its best score.
LEGACY_GAME_MAX = 100

# What the show was called on the poster, for the line above the numbers.
ARCHIVE_DATES: dict[str, str] = {
    "uae_ai_connect": "Sep. 29-30",
}

ARCHIVE_KEYS: frozenset[str] = frozenset(ARCHIVES)


def is_archived(key: str) -> bool:
    return key in ARCHIVE_KEYS


def facts(key: str) -> str:
    """`2 rooms · 2 days · 5 games · 30 mins per round`, in that order.

    "rooms" for the 1.0 show and "booth" for the 2.0 ones: the old event ran two
    game rooms, the newer ones a single stand. Built from the counts rather than
    stored as one string so the numbers and the sentence cannot drift apart.
    """
    counted = ARCHIVE_FACTS[key]
    # 1.0 scored every game out of 100, so the total on offer is games x 100.
    # Written as a product rather than a fifth dated number: the games count is
    # already above, and the two cannot drift apart this way.
    return (
        f"{counted['rooms']} rooms · {counted['days']} days · "
        f"{counted['games']} games · {counted['mins']} mins per round · "
        f"{counted['games'] * LEGACY_GAME_MAX:,} points in total"
    )


@lru_cache(maxsize=None)
def _raw(filename: str) -> dict:
    return json.loads((DATA / filename).read_text(encoding="utf-8"))


def row(key: str) -> dict:
    """The tab for an archived event: a label, a headcount and its generation.

    `players` is the old system's own participant count, so the tab badge means
    the same thing it means on a 2.0 event, while `source` tells the console that
    nothing else on the page can be compared with it.
    """
    label, filename = ARCHIVES[key]
    raw = _raw(filename)
    return {
        "key": key,
        "label": label,
        "tab": "UAE AI Connect",
        "title": f"{label} ({ARCHIVE_DATES[key]})",
        "dates": ARCHIVE_DATES[key],
        "players": raw["total_participants"],
        "last_registration": raw["event"]["end_at"],
        "source": "arcade-1.0",
    }


def report(key: str) -> dict:
    """One archived event, shaped for the console's archive view.

    Deliberately not the live report shape: `headline` and `games` carry the old
    scale (0-100 per game) and `leaderboard` is the export's top ten, so the front
    end cannot accidentally reuse a component that assumes 2.0 sections.
    """
    label, filename = ARCHIVES[key]
    raw = _raw(filename)
    showdown = raw["showdown"]
    return {
        "key": key,
        "label": label,
        "tab": "UAE AI Connect",
        "title": f"{label} ({ARCHIVE_DATES[key]})",
        "dates": ARCHIVE_DATES[key],
        "source": "arcade-1.0",
        "event": raw["event"],
        # What the event was, in the room's own words. The page prints this line
        # above the numbers, so it is data rather than copy baked into the JSX.
        "tagline": f"Arcade 1.0 · AI Cyber Arcade for UAE AI Connect - {facts(key)}",
        "headline": {
            "participants": raw["total_participants"],
            "active_players": raw["active_players"],
            "countries": raw["country_count"],
            "rounds": showdown["rooms"],
            "round_players": showdown["players"],
            "answers": showdown["answers"],
        },
        "countries": raw["countries"],
        "registrations_per_day": raw["registrations_per_day"],
        # A perfect game in 1.0 is 100, so the share of it is a fair reading of a
        # row. It is *not* comparable with a 2.0 `Of max`, and the page says so.
        "games": [
            {
                "key": game_key,
                "label": game_label,
                "players": raw["games"][game_key]["players"],
                "average": raw["games"][game_key]["avg"],
                "best": raw["games"][game_key]["max"],
                "max": 100,
            }
            for game_key, game_label in LEGACY_GAMES
        ],
        "leaderboard": [
            {
                "rank": index + 1,
                "nickname": player["nickname"],
                "name": f"{player['firstname'] or ''} {player['lastname'] or ''}".strip(),
                "region": player["region"],
                "total_score": player["total_score"],
            }
            for index, player in enumerate(raw["top_players"])
        ],
        "leaderboard_note": "top ten only - the 1.0 export has no full score list",
    }
