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
15s to answer plus 10s reading the verdict for Hallucination, 20s plus 8s for the
quiz, and the verdict page advances itself so nobody has to press anything. Every
node also keeps a tail after its last question so the closing score is not cut
off - ``duration_sec`` is the cycles plus that tail.

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

Every node also carries ``requires``: the module it belongs to. A room is built
from a set of modules (see ``build_flow``), so hiding a game means building a
shorter list rather than teaching the engine to skip. Nothing is ever "skipped"
at runtime, which is why a hidden module cannot leave a screen waiting on a node
that is not there. The key is declared on the node instead of inferred from
``game`` / ``section`` so that a new node which forgets to name its module fails
a test instead of turning up in every room.
"""

from __future__ import annotations

from collections.abc import Iterable

# A section board is the only place the room sees the standings move, and a
# screen that hears about the node a second or two late used to lose most of it.
# Eight seconds is long enough that a hiccup costs a beat instead of the board.
BOARD_SECONDS = 8

# Hallucination's board is shorter because the room has already been looking at
# one: the wall shows the standings through the 10s review of every question, so
# the node only has to carry the iPad's own reveal plus a hand-over beat. The
# wall therefore reads fifteen seconds of standings in a row, the device ten of
# review and five of standings - which is what the round was written to be.
HALLUCINATION_BOARD_SECONDS = 5

# What a room is built from. Four games, plus the three pieces of furniture
# around them, which are separate because a shorter show may not want them
# either. `total` keeps the section name the flow already uses for that board.
MODULES: tuple[str, ...] = (
    "welcome",
    "hallucination",
    "data",
    "sabotage",
    "ultimate",
    "total",
    "wrapup",
)

# Sabotage's slot, from the engine's own clock (sabotage_config.TIMINGS): 10s
# draw + 15s rules + 40s first pick + 3 x 30s picks + 4 x 8s reveals + 5s final
# = 192s, plus 3s so a slow team draw cannot push the last reveal past the cut.
SABOTAGE_SLOT_SECONDS = 195

# Intro - [opening] Hallucination - [opening] Data Shadow - Sabotage
# - [opening] Ultimate Showdown - Total - Wrap up
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
    {"type": "intro", "title": "Welcome", "duration_sec": 10, "requires": "welcome"},
    {
        "type": "opening",
        "game": "hallucination",
        "title": "AI Hallucination",
        "duration_sec": 15,
        "title_sec": 3,
        "section": "hallucination",
        "requires": "hallucination",
    },
    {
        "type": "game",
        "game": "hallucination",
        "title": "Can you trust it",
        # 3 x (20s answer + 10s review). No closing page: the last review hands
        # straight over to the leaderboard, so `closing_sec` is zero here.
        "duration_sec": 90,
        "closing_sec": 0,
        "questions": 3,
        "seconds_each": 30,
        # The 30 is a whole cycle: 20s of answering, then 10s of review. The
        # wall's clock counts the window the room can act in, so it needs the
        # split - without it the big screen reads ten seconds longer than the
        # iPad in a player's hand. The review half is also where the wall switches
        # to the standings while the devices read the answer.
        "answer_sec": 20,
        "score": "hallucination",
        "requires": "hallucination",
    },
    {
        "type": "leaderboard",
        "section": "hallucination",
        "title": "Hallucination Standings",
        "duration_sec": HALLUCINATION_BOARD_SECONDS,
        "requires": "hallucination",
    },
    {
        "type": "opening",
        "game": "data_shadow",
        "title": "Data Shadows",
        "duration_sec": 15,
        "title_sec": 3,
        "section": "data",
        "requires": "data",
    },
    {
        "type": "game",
        "game": "data_shadow",
        "title": "Data Shadows",
        # 50s of reading and tapping paragraphs, plus 10s that reveal what
        # leaked - the answer is withheld until the clock runs out, so the tail
        # is the game.
        "duration_sec": 60,
        # 50s of reading and tapping, 10s of reveal. The split is here for the
        # same reason: the wall clock has to match the device.
        "answer_sec": 50,
        "score": "data",
        "requires": "data",
    },
    {
        "type": "leaderboard",
        "section": "data",
        "title": "Data Shadows Standings",
        "duration_sec": BOARD_SECONDS,
        "requires": "data",
    },
    # No `opening`: AI Cyber Battle draws its teams and then shows its own rules
    # card inside the node, so a flow-level card would introduce the game twice.
    {
        "type": "game",
        "game": "sabotage",
        # The name the audience reads. The key stays `sabotage`: it is the
        # section, the score column and the API path, none of which are copy.
        "title": "AI Cyber Battle",
        # The engine runs its own clock (see sabotage_config.TIMINGS: 10s draw +
        # 15s rules + 40s first pick + 3 x 30s picks + 4 x 8s reveals + 5s final
        # = 192s); this slot only has to be long enough, and 3s more so a slow
        # team draw cannot push the last reveal past the cut.
        "duration_sec": SABOTAGE_SLOT_SECONDS,
        "score": "sabotage",
        "requires": "sabotage",
    },
    {
        "type": "leaderboard",
        "section": "sabotage",
        "title": "AI Cyber Battle Standings",
        "duration_sec": BOARD_SECONDS,
        "requires": "sabotage",
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
        "requires": "ultimate",
    },
    {
        "type": "quiz",
        "title": "Ultimate Showdown",
        # 3 x (20s answer + 8s review) + 2 x 3s bonus card + 3s of closing
        # score. No `intro_sec`: the first question is on screen from the first
        # second. The three are drawn on the device from a seed, so the count is
        # fixed and only the questions vary - the node length never depends on
        # the draw.
        #
        # `bonus_questions` is 1-based and has to name the slots the drawing
        # table multiplies (slots 2 and 3); the iPad warns in development if the
        # two ever disagree.
        "duration_sec": 93,
        "questions": 3,
        "seconds_each": 28,
        # 20s per question plus the 8s review folded into `seconds_each`.
        "answer_sec": 20,
        "bonus_intro_sec": 3,
        "bonus_questions": [2, 3],
        "score": "ultimate",
        "requires": "ultimate",
    },
    {
        "type": "leaderboard",
        "section": "total",
        "title": "Activity Leaderboard",
        "duration_sec": 10,
        "requires": "total",
    },
    {"type": "outro", "title": "Wrap up", "duration_sec": 10, "requires": "wrapup"},
]


def build_flow(modules: Iterable[str] | None = None) -> list[dict]:
    """The rundown for a room that runs ``modules``, in the show's own order.

    ``None`` means every module, which is what a room created before this
    existed gets. Unknown keys are ignored here and rejected at the API, so this
    function stays total: it can always produce a list for the engine to walk.

    An empty result is possible only if the caller asks for nothing; the room
    endpoint refuses that, because an engine with no nodes would finish on its
    first tick and leave every screen on the wrap-up of a show that never ran.
    """
    keep = set(MODULES if modules is None else modules)
    # A fresh dict per node: rooms mutate nothing today, but the engine already
    # deep-copies `ROUND_FLOW`, and handing out the module-level dicts would put
    # one room's future edit in every other room's flow.
    return [dict(node) for node in ROUND_FLOW if node["requires"] in keep]
