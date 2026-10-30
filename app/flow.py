"""Main-line flow config. Edit the list to change the show, not the engine.

A node has two identifying fields:

* ``type`` -- the **category**, and the only thing the engine cares about. It
  picks the layout, so every node in a category is staged the same way.
* ``game`` -- the **specific game**, present only on ``game`` nodes. Two games
  can look nothing alike while sharing the category, which is why the frontend
  dispatches on ``game`` for its own screen while ``type`` stays broad.

``title`` is display-only: the audience reads it, but no code branches on it.
There is one quiz left in the show - the Ultimate - so nothing has to tell the
quizzes apart any more, but the split stays: copy is not structure.

Durations for the built screens are content-driven: the node has to outlast its
questions, or the engine advances mid-question. A question is a fixed cycle:
25s to answer plus 5s reading the verdict for Hallucination, 20s plus 5s for the
quiz, and the verdict page advances itself so nobody has to press anything.
Every node also keeps a tail after its last question so the closing score is not
cut off - ``duration_sec`` is the cycles plus that tail.

``intro_sec`` is subtracted before ``seconds_each`` is applied, so the question
index the big screen shows is still derived from the node clock alone. No node
sets it today: the Ultimate quiz starts on its first question, because the brief
card in front of it has already announced the round.

``questions`` + ``seconds_each`` also let the engine say *which* question is on
screen right now (see ``Engine.snapshot``). The big screen needs that to show the
same question every iPad is answering; without it the question would only exist
inside each device.

``title`` nodes are the 5s name card in front of each game, and ``brief`` nodes
are the 15s "how to play" card that follows it. The Ultimate quiz has a brief and
no name card: the last round goes straight from the rules into the questions. ``title`` and ``brief`` each name
which game's wording to show; the engine treats both as ordinary timed nodes, so
nothing in the engine knows about intros. ``section`` is there so the console
groups the cards with the section they introduce.

The order is deliberate: the name lands first, so the room knows what it is about
to play, then the rules, then the game. A ``brief`` still sits immediately before
the node it introduces, which is what the console's grouping relies on.
"""

# Intro - [title, brief] Hallucination - [title, brief] Data Shadow
# - [title] Sabotage - [brief] Ultimate Quiz - Wrap up
#
# Three of those nodes are ``brief`` cards: 15 seconds of rules in front of each
# game, so nobody has to be told what to press on stage.
#
# Scoring nodes name the partition their points belong to (`score`). One column
# per section, so the board reads Hallucination / Data / Sabotage / Ultimate
# rather than one column per node. The server maps this key to a `scores`
# column; clients never name the column themselves.
ROUND_FLOW: list[dict] = [
    # The welcome lays out the four games one card at a time, so the slot has to
    # outlast the reveal (2.2s lead-in + 4 cards a second apart + reading time).
    {"type": "intro", "title": "Welcome", "duration_sec": 12},
    {"type": "title", "title": "hallucination", "duration_sec": 5, "section": "hallucination"},
    {
        "type": "brief",
        "brief": "hallucination",
        "title": "How Hallucination works",
        "duration_sec": 15,
        "section": "hallucination",
    },
    {
        "type": "game",
        "game": "hallucination",
        "title": "Hallucination",
        # 3 x (25s answer + 5s verdict) + 6s of closing score.
        "duration_sec": 96,
        "questions": 3,
        "seconds_each": 30,
        "score": "hallucination",
    },
    {
        "type": "leaderboard",
        "section": "hallucination",
        "title": "Hallucination standings",
        # Long enough for the count-up and the reorder to be read.
        "duration_sec": 8,
    },
    {"type": "title", "title": "data_shadow", "duration_sec": 5, "section": "data"},
    {
        "type": "brief",
        "brief": "data_shadow",
        "title": "How Data Shadow works",
        "duration_sec": 15,
        "section": "data",
    },
    {"type": "game", "game": "data_shadow", "title": "Data Shadow", "duration_sec": 86, "score": "data"},
    {"type": "leaderboard", "section": "data", "title": "Data Shadow standings", "duration_sec": 8},
    {"type": "title", "title": "sabotage", "duration_sec": 5, "section": "sabotage"},
    # No `brief` node here on purpose: Sabotage draws its teams and then shows
    # its own 20-second rules card inside the node, so a flow-level card would
    # introduce the game before the game introduces itself.
    {
        "type": "game",
        "game": "sabotage",
        "title": "Sabotage",
        # The Sabotage engine runs its own clock (~4 minutes plus tie-break
        # headroom) and broadcasts it over WS; this node only holds the slot
        # in the main flow for as long as the game needs.
        # Sabotage v2 runs its own clock (8s draw + 20s rules + 50s first pick +
        # 3 x 35s picks + 4 x 10s reveals + 20s final = 243s); this slot only
        # has to be long enough.
        "duration_sec": 250,
        "score": "sabotage",
    },
    {
        "type": "leaderboard",
        "section": "sabotage",
        "title": "Sabotage standings",
        "duration_sec": 8,
    },
    # No `title` node: the last round is announced by its rules card and then
    # goes straight into the questions.
    {
        "type": "brief",
        "brief": "ultimate_quiz",
        "title": "How the Ultimate Quiz works",
        "duration_sec": 15,
        "section": "ultimate",
    },
    {
        "type": "quiz",
        "title": "Ultimate Quiz",
        # 5 x (20s answer + 5s verdict) + 5s of closing score. No `intro_sec`:
        # the first question is on screen from the first second.
        "duration_sec": 130,
        "questions": 5,
        "seconds_each": 25,
        "score": "ultimate",
    },
    {
        "type": "leaderboard",
        "section": "ultimate",
        "title": "Ultimate standings",
        "duration_sec": 8,
    },
    # The final, activity-wide ranking. A placeholder until its design lands.
    {"type": "leaderboard", "section": "total", "title": "Activity Leaderboard", "duration_sec": 5},
    {"type": "outro", "title": "Wrap up", "duration_sec": 10},
]
