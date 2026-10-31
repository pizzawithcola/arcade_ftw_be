"""Sabotage v2 configuration: four rounds of three-dimensional card play.

Everything here is data, so tuning the game never means touching the engine.

A card carries a vector over three domains - data, people, infra - and carries
a price in dollars. Each round has one *target* domain: only that column decides
who wins the round; the other two decide how badly. Prices are deliberately not
a power ranking: a cheap card can be the strongest thing in the deck and an
expensive one can be a trap, which is what stops "buy the dearest card" from
being a strategy.

Money is the whole game. A team shares one `TOTAL_BUDGET` across all four
rounds, so a round cannot be bought on its own: the best hand in each round
costs about $27,000, four of them cost $108,000, and there is only $100,000.
Playing all three early rounds at full strength leaves too little for the last
one, which is the planning pressure the reveal is meant to expose.

Balance rules the tests enforce:

* under an unlimited budget the best reachable target column is exactly one
  point higher for the attacker than for the defender - one point is the whole
  margin, so a distracted attacker does not break through and a defender who
  buys one decoy holds;
* the cheapest hand that reaches that ceiling costs between $25,000 and $30,000,
  so a single round cannot swallow the match;
* the four best hands together cost more than the budget, so the money has to
  be spent unevenly somewhere.

Re-derive `SOLO_SCORE` whenever a vector or a price moves.
"""

from __future__ import annotations

# --- budget and deck limits ---------------------------------------------------
# One pool per team, spent across all four rounds. Amounts are dollars.
TOTAL_BUDGET = 100_000
MAX_CARDS = 4

# A room with a single player cannot field two teams; that player is paid the
# best total a team can reach (four rounds won outright), so the section is
# never a zero on the leaderboard.
SOLO_SCORE = 2400

# --- domains ------------------------------------------------------------------
DATA = "data"
PEOPLE = "people"
INFRA = "infra"
DOMAINS = (DATA, PEOPLE, INFRA)

DOMAIN_LABEL = {DATA: "Data", PEOPLE: "People", INFRA: "Infra"}

# --- timing, in seconds -------------------------------------------------------
# The whole match is 160s: 10 draw + 15 rules + 35 first pick + 3 x 25 picks
# + 4 x 5 reveals + 5 final. The flow slot for this game is a few seconds longer
# (see flow.py); these are the only numbers the engine counts.
TIMINGS = {
    "team_reveal": 10,
    "brief": 15,
    # Choosing now means choosing what to save, so the pick window is a little
    # longer than a card-a-round game needs.
    "select": 25,
    # The first round also teaches the interface, so it gets longer.
    "select_first": 35,
    "reveal": 5,
    "final": 5,
}

CARDS: dict[str, dict] = {
    # --- round 1: phishing email (target data) --- attacker
    "fake_sign_in_page": {
        "name": "Fake Sign-In Page",
        "blurb": "A copy of our sign-in page on a look-alike address",
        "cost": 8_000,
        "vector": (3, 0, 1),
    },
    "malicious_attachment": {
        "name": "Malicious Attachment",
        "blurb": "An invoice that installs software when it is opened",
        "cost": 13_000,
        "vector": (3, 1, 1),
    },
    "personal_details": {
        "name": "Personal Details",
        "blurb": "Their name, their manager and their project",
        "cost": 4_000,
        "vector": (2, 2, 0),
    },
    "fake_sender_name": {
        "name": "Fake Sender Name",
        "blurb": "The sender name the employee already trusts",
        "cost": 2_000,
        "vector": (2, 1, 1),
    },
    "urgent_deadline": {
        "name": "Urgent Deadline",
        "blurb": "The account is closed if it is not acted on before 5pm",
        "cost": 1_000,
        "vector": (1, 1, 0),
    },
    # --- round 1: phishing email (target data) --- defender
    "block_spam": {
        "name": "Block Spam Emails",
        "blurb": "Blocks known bad senders and attachments",
        "cost": 13_000,
        "vector": (3, 0, 2),
    },
    "verify_sender": {
        "name": "Verify the Sender",
        "blurb": "Checks the real address, not the display name",
        "cost": 6_000,
        "vector": (3, 0, 1),
    },
    "scan_link": {
        "name": "Scan the Link",
        "blurb": "Shows where a link leads before it is opened",
        "cost": 5_000,
        "vector": (2, 0, 0),
    },
    "security_training": {
        "name": "Security Training",
        "blurb": "Staff recognise a phishing email before clicking",
        "cost": 3_000,
        "vector": (1, 2, 0),
    },
    "report_phishing": {
        "name": "Report Phishing",
        "blurb": "One button, and the mail team takes over",
        "cost": 2_000,
        "vector": (1, 1, 1),
    },
    # --- round 2: CEO impersonation (target people) --- attacker
    "deepfake_video": {
        "name": "Deepfake Video Call",
        "blurb": "A live video call with the CEO's face and voice",
        "cost": 14_000,
        "vector": (1, 3, 0),
    },
    "urgent_wire": {
        "name": "Urgent Wire Transfer",
        "blurb": "An acquisition that closes tonight",
        "cost": 6_000,
        "vector": (1, 3, 0),
    },
    "lookalike_website": {
        "name": "Lookalike Website",
        "blurb": "The website is masquerading as legitimate",
        "cost": 2_000,
        "vector": (2, 0, 1),
    },
    "do_not_escalate": {
        "name": "Do Not Escalate",
        "blurb": "Coerces employees into not escalating",
        "cost": 4_000,
        "vector": (0, 2, 0),
    },
    "vishing_call": {
        "name": "Vishing Call",
        "blurb": "A phone call from someone who sounds like the CEO",
        "cost": 3_000,
        "vector": (1, 2, 0),
    },
    # --- round 2: CEO impersonation (target people) --- defender
    "callback_verification": {
        "name": "Callback Verification",
        "blurb": "Calls back to a verified number",
        "cost": 9_000,
        "vector": (0, 3, 0),
    },
    "payment_approval": {
        "name": "Payment Approval",
        "blurb": "Requires verification and authorisation of the payee",
        "cost": 7_000,
        "vector": (1, 2, 0),
    },
    "verify_identity": {
        "name": "Identity Verification",
        "blurb": "Validate the caller is who they say they are",
        "cost": 6_000,
        "vector": (0, 2, 0),
    },
    "escalate_manager": {
        "name": "Manager Escalation",
        "blurb": "Gets a second opinion to verify the request",
        "cost": 5_000,
        "vector": (0, 2, 1),
    },
    "address_check": {
        "name": "Hidden Address Check",
        "blurb": "Reads the address behind the display name",
        "cost": 2_000,
        "vector": (1, 0, 0),
    },
    # --- round 3: manipulating the assistant (target infra) --- attacker
    "malicious_script": {
        "name": "Malicious AI Script",
        "blurb": "An instruction buried in a document that the assistant reads",
        "cost": 9_000,
        "vector": (1, 0, 3),
    },
    "hidden_script": {
        "name": "Hidden AI Script",
        "blurb": "White text the assistant reads and the reviewer does not",
        "cost": 8_000,
        "vector": (1, 1, 3),
    },
    "jailbreak": {
        "name": "Jailbreak the Assistant",
        "blurb": "Persuades the assistant to ignore its safety rules",
        "cost": 6_000,
        "vector": (0, 2, 2),
    },
    "exfiltrate": {
        "name": "Exfiltrate Data",
        "blurb": "Makes the assistant send its notes to an outside address",
        "cost": 4_000,
        "vector": (2, 0, 2),
    },
    "false_rule": {
        "name": "False Rule",
        "blurb": "Gives the assistant a rule that was never approved",
        "cost": 2_500,
        "vector": (1, 1, 1),
    },
    # --- round 3: manipulating the assistant (target infra) --- defender
    "read_only": {
        "name": "Read-Only Access",
        "blurb": "The assistant can read, but cannot act without a person approving it",
        "cost": 11_000,
        "vector": (1, 0, 3),
    },
    "human_in_the_loop": {
        "name": "Human in the Loop",
        "blurb": "A human reviews the agent's tasks",
        "cost": 8_000,
        "vector": (1, 1, 2),
    },
    "scan_file": {
        "name": "Scan the File First",
        "blurb": "Removes hidden instructions before the file is read",
        "cost": 6_000,
        "vector": (2, 0, 3),
    },
    "assistant_restrictions": {
        "name": "Assistant Restrictions",
        "blurb": "Limits the assistant cannot be talked out of",
        "cost": 2_000,
        "vector": (1, 0, 1),
    },
    "human_approval": {
        "name": "Human Approval",
        "blurb": "A person approves the final action",
        "cost": 1_500,
        "vector": (1, 1, 0),
    },
    # --- round 4: poisoning the assistant's sources (target data) --- attacker
    "poison_source": {
        "name": "Poison the Data Source",
        "blurb": "Malicious script in a trusted data store",
        "cost": 14_000,
        "vector": (3, 1, 0),
    },
    "insider_edit": {
        "name": "Malicious Insider Edit",
        "blurb": "An editor with access and a reason to misuse it",
        "cost": 7_000,
        "vector": (3, 2, 0),
    },
    "auto_sync": {
        "name": "Unchecked Auto-Sync",
        "blurb": "The sync pulls in any page it finds, unchecked",
        "cost": 4_500,
        "vector": (0, 0, 2),
    },
    "outdated_upload": {
        "name": "Outdated File Upload",
        "blurb": "A 2021 policy, uploaded as today's version",
        "cost": 4_000,
        "vector": (2, 0, 0),
    },
    # the same card, played again in round 4: one entry, two decks
    # --- round 4: poisoning the assistant's sources (target data) --- defender
    "verify_source": {
        "name": "Verify the Source",
        "blurb": "Every page traces back to a named owner",
        "cost": 10_000,
        "vector": (3, 0, 1),
    },
    "approved_sources": {
        "name": "Approved Sources Only",
        "blurb": "Anything unsigned is not published",
        "cost": 7_000,
        "vector": (3, 0, 2),
    },
    "human_approval_signoff": {
        "name": "Human Approval",
        "blurb": "A person approves what gets published",
        "cost": 5_000,
        "vector": (2, 2, 0),
    },
    "source_list": {
        "name": "Approved Source List",
        "blurb": "The sync reads only the listed websites",
        "cost": 5_000,
        "vector": (1, 0, 2),
    },
    "source_required": {
        "name": "Source Required",
        "blurb": "No verification of the source data, so the agent does not accept it",
        "cost": 1_500,
        "vector": (0, 0, 1),
    },
}

# --- rounds -------------------------------------------------------------------
# Attack and defence alternate sides so each team attacks exactly twice, which
# is what keeps the match symmetric: the attacker holds the one-point
# structural advantage, so an unbalanced schedule would decide the winner before
# anyone plays. Five cards a side, at most four of them buyable in a round.
ROUNDS: list[dict] = [
    {
        "round": 1,
        "theme": "Phishing email",
        "target": DATA,
        "attacker": "red",
        "defender": "blue",
        "attack_brief": "Get a credential out of the organisation.",
        "defence_brief": "Keep the credential inside the organisation.",
        "attack": [
            "fake_sign_in_page",
            "malicious_attachment",
            "personal_details",
            "fake_sender_name",
            "urgent_deadline",
        ],
        "defence": [
            "block_spam",
            "verify_sender",
            "scan_link",
            "security_training",
            "report_phishing",
        ],
    },
    {
        "round": 2,
        "theme": "CEO impersonation",
        "target": PEOPLE,
        "attacker": "blue",
        "defender": "red",
        "attack_brief": "Get a person to authorise something they should not.",
        "defence_brief": "Make authorisation survive a phone call.",
        "attack": [
            "deepfake_video",
            "urgent_wire",
            "lookalike_website",
            "do_not_escalate",
            "vishing_call",
        ],
        "defence": [
            "callback_verification",
            "payment_approval",
            "verify_identity",
            "escalate_manager",
            "address_check",
        ],
    },
    {
        "round": 3,
        "theme": "Manipulating the AI assistant",
        "target": INFRA,
        "attacker": "red",
        "defender": "blue",
        "attack_brief": "Make the assistant do what its instructions forbid.",
        "defence_brief": "Keep the assistant inside its own rules.",
        "attack": [
            "malicious_script",
            "hidden_script",
            "jailbreak",
            "exfiltrate",
            "false_rule",
        ],
        "defence": [
            "read_only",
            "human_in_the_loop",
            "scan_file",
            "assistant_restrictions",
            "human_approval",
        ],
    },
    {
        "round": 4,
        "theme": "Poisoning the AI's sources",
        "target": DATA,
        "attacker": "blue",
        "defender": "red",
        "attack_brief": "Make the assistant tell a customer something false.",
        "defence_brief": "Prove every answer came from a trusted source.",
        "attack": [
            "poison_source",
            "insider_edit",
            "auto_sync",
            "outdated_upload",
            "lookalike_website",
        ],
        "defence": [
            "verify_source",
            "approved_sources",
            "human_approval_signoff",
            "source_list",
            "source_required",
        ],
    },
]

# --- scoring ------------------------------------------------------------------
# Keyed by (broke the target, number of flanking domains also broken). Always
# sums to 600, and every breakthrough row beats every held row - so the side
# that wins the round also scores more, which "count the breaches" did not
# guarantee.
POINTS: dict[tuple[bool, int], tuple[int, int]] = {
    (False, 0): (0, 600),
    (False, 1): (120, 480),
    (False, 2): (240, 360),
    (True, 0): (320, 280),
    (True, 1): (420, 180),
    (True, 2): (600, 0),
}

OUTCOME_NAMES: dict[tuple[bool, int], str] = {
    (False, 0): "Full defence",
    (False, 1): "Flank leak",
    (False, 2): "Severe flank leak",
    (True, 0): "Narrow breach",
    (True, 1): "Effective breach",
    (True, 2): "Total compromise",
}


def cards_for(round_index: int, role: str) -> list[str]:
    """The six cards a side picks from in a round."""
    round_spec = ROUNDS[round_index]
    return list(round_spec["attack" if role == "attacker" else "defence"])


def vector_of(card_ids) -> tuple[int, int, int]:
    """Sum of a build's vectors."""
    total = [0, 0, 0]
    for card_id in card_ids:
        for index, value in enumerate(CARDS[card_id]["vector"]):
            total[index] += value
    return (total[0], total[1], total[2])


def cost_of(card_ids) -> int:
    return sum(CARDS[card_id]["cost"] for card_id in card_ids)


def score_round(
    attack: tuple[int, int, int],
    defence: tuple[int, int, int],
    target: str,
) -> dict:
    """Compare two vectors on one target and its two flanks.

    Pure: no engine state, which is what lets the balance tests exhaust every
    affordable combination without spinning up a game.
    """
    target_index = DOMAINS.index(target)
    broke_target = attack[target_index] > defence[target_index]
    flanks = 0
    rows = []
    for index, domain in enumerate(DOMAINS):
        attacker_ahead = attack[index] > defence[index]
        if index != target_index and attacker_ahead:
            flanks += 1
        if attacker_ahead:
            result = "break"
        elif attack[index] == defence[index]:
            result = "tie"
        else:
            result = "hold"
        rows.append(
            {
                "domain": domain,
                "attack": attack[index],
                "defence": defence[index],
                # A tie holds the line: the defender does not have to outspend
                # an attack, only match it.
                "result": result,
                "target": index == target_index,
            }
        )
    attack_points, defence_points = POINTS[(broke_target, flanks)]
    return {
        "broke_target": broke_target,
        "flanks": flanks,
        "outcome": OUTCOME_NAMES[(broke_target, flanks)],
        "dims": rows,
        "attack_points": attack_points,
        "defence_points": defence_points,
    }
