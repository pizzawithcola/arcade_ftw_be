"""Sabotage v2 configuration: four rounds of three-dimensional card play.

Everything here is data, so tuning the game never means touching the engine.

A card carries a vector over three domains - data, people, infra - and costs
part of the team's 100-point budget. Each round has one *target* domain: only
that column decides who wins the round; the other two decide how badly. Prices
are deliberately not a power ranking: a cheap card can be the strongest thing
in the deck and an expensive one can be a trap, which is what stops "buy the
dearest card" from being a strategy.

Balance rule the tests enforce: under the 100-point budget the best reachable
target column is exactly one point higher for the attacker than for the
defender. One point is the whole margin - an attacker who spends badly does not
break through, and a defender who buys one distraction holds. Re-derive
`SOLO_SCORE` whenever a vector or a price moves.
"""

from __future__ import annotations

# --- budget and deck limits ---------------------------------------------------
BUDGET = 100
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
TIMINGS = {
    "team_reveal": 8,
    "brief": 20,
    "select": 30,
    # The first round also teaches the interface, so it gets longer.
    "select_first": 40,
    "reveal": 10,
    "final": 20,
}

# --- cards --------------------------------------------------------------------
# `vector` is (data, people, infra), each 0-3. `blurb` is one line: the phone
# screens have no room for more, and a card has to be understood at a glance.
CARDS: dict[str, dict] = {
    # round 1 attack: phishing email
    "cloned_login_page": {
        "name": "Cloned Login Page",
        "blurb": "A pixel-perfect copy of the intranet sign-in",
        "cost": 26,
        "vector": (3, 0, 1),
    },
    "malicious_attachment": {
        "name": "Malicious Attachment",
        "blurb": "An invoice that runs code when opened",
        "cost": 42,
        "vector": (2, 1, 1),
    },
    "personalised_context": {
        "name": "Personalised Context",
        "blurb": "Their name, their manager, their project",
        "cost": 18,
        "vector": (1, 2, 0),
    },
    "spoofed_sender": {
        "name": "Spoofed Sender",
        "blurb": "The display name is the one they trust",
        "cost": 28,
        "vector": (2, 0, 0),
    },
    "urgent_deadline": {
        "name": "Urgent Deadline",
        "blurb": "Act before 5pm or the account closes",
        "cost": 14,
        "vector": (1, 1, 0),
    },
    "voice_call_followup": {
        "name": "Voice Call Follow-up",
        "blurb": "A call from IT to walk them through it",
        "cost": 30,
        "vector": (0, 3, 0),
    },
    # round 1 defence: phishing email
    "email_gateway": {
        "name": "Email Gateway Filter",
        "blurb": "Block known-bad senders and attachments",
        "cost": 40,
        "vector": (3, 0, 1),
    },
    "security_training": {
        "name": "Security Training",
        "blurb": "Staff can spot a phish before clicking",
        "cost": 16,
        "vector": (1, 2, 0),
    },
    "verify_sender": {
        "name": "Verify Sender",
        "blurb": "Check the real address, not the name",
        "cost": 26,
        "vector": (1, 0, 1),
    },
    "hover_before_click": {
        "name": "Hover Before Click",
        "blurb": "Read where the link actually goes",
        "cost": 14,
        "vector": (1, 0, 0),
    },
    "report_suspicious": {
        "name": "Report Suspicious",
        "blurb": "One button, and the mail team is on it",
        "cost": 12,
        "vector": (0, 1, 1),
    },
    "verify_payment": {
        "name": "Verify Payment",
        "blurb": "Call finance before any transfer",
        "cost": 32,
        "vector": (0, 0, 2),
    },
    # round 2 attack: CEO impersonation
    "deepfake_voice": {
        "name": "Deepfake Voice Call",
        "blurb": "Forty seconds of sample is all it needs",
        "cost": 28,
        "vector": (1, 3, 0),
    },
    "deepfake_video": {
        "name": "Deepfake Video",
        "blurb": "A live-looking call with the CFO",
        "cost": 38,
        "vector": (1, 2, 0),
    },
    "urgent_wire": {
        "name": "Urgent Wire Transfer",
        "blurb": "An acquisition, and it closes tonight",
        "cost": 18,
        "vector": (1, 1, 0),
    },
    "lookalike_domain": {
        "name": "Lookalike Domain",
        "blurb": "Two letters swapped, same logo",
        "cost": 30,
        "vector": (2, 0, 0),
    },
    "authority_pressure": {
        "name": "Authority Pressure",
        "blurb": "Do not escalate this to anyone",
        "cost": 24,
        "vector": (0, 2, 0),
    },
    "malicious_login": {
        "name": "Malicious Login Page",
        "blurb": "Harvest a session token for the replay",
        "cost": 34,
        "vector": (2, 0, 1),
    },
    # round 2 defence: CEO impersonation
    "payment_approval": {
        "name": "Payment Approval",
        "blurb": "Two signatures, no exceptions",
        "cost": 26,
        "vector": (1, 1, 0),
    },
    "separate_channel": {
        "name": "Verify Separate Channel",
        "blurb": "Call back on a number you already had",
        "cost": 34,
        "vector": (0, 2, 0),
    },
    "verify_identity": {
        "name": "Verify Identity",
        "blurb": "Ask something only the real person knows",
        "cost": 18,
        "vector": (0, 2, 0),
    },
    "escalate_manager": {
        "name": "Escalate to Manager",
        "blurb": "Nobody should decide this alone",
        "cost": 30,
        "vector": (0, 2, 1),
    },
    "check_domain": {
        "name": "Check Domain Header",
        "blurb": "Read the header the display hides",
        "cost": 12,
        "vector": (1, 0, 0),
    },
    "inspect_link": {
        "name": "Inspect Link",
        "blurb": "Open it in a sandbox, never on the laptop",
        "cost": 40,
        "vector": (0, 0, 1),
    },
    # round 3 attack: AI assistant manipulation
    "prompt_injection": {
        "name": "Prompt Injection",
        "blurb": "Instructions hidden inside a document",
        "cost": 22,
        "vector": (1, 0, 3),
    },
    "hidden_instruction": {
        "name": "Hidden Instruction",
        "blurb": "White text the model reads and you do not",
        "cost": 40,
        "vector": (1, 1, 2),
    },
    "jailbreak": {
        "name": "Jailbreak",
        "blurb": "Talk it out of its own guardrails",
        "cost": 34,
        "vector": (0, 1, 2),
    },
    "data_exfiltration": {
        "name": "Data Exfiltration",
        "blurb": "Make the assistant send its context out",
        "cost": 28,
        "vector": (2, 0, 1),
    },
    "false_context": {
        "name": "False Context",
        "blurb": "Feed it a rule that is not a rule",
        "cost": 16,
        "vector": (1, 1, 1),
    },
    "fake_ceo_email": {
        "name": "Fake CEO Email",
        "blurb": "The assistant is told the CEO approved",
        "cost": 30,
        "vector": (1, 1, 0),
    },
    # round 3 defence: AI assistant manipulation
    "least_privilege": {
        "name": "Least Privilege",
        "blurb": "The assistant can read, not wire",
        "cost": 36,
        "vector": (1, 0, 2),
    },
    "input_sanitisation": {
        "name": "Input Sanitisation",
        "blurb": "Strip instructions before they reach it",
        "cost": 20,
        "vector": (2, 0, 1),
    },
    "output_review": {
        "name": "Output Review",
        "blurb": "Nothing leaves without a second look",
        "cost": 28,
        "vector": (1, 0, 2),
    },
    "human_in_the_loop": {
        "name": "Human in the Loop",
        "blurb": "A person signs the final action",
        "cost": 14,
        "vector": (1, 1, 0),
    },
    "model_guardrails": {
        "name": "Model Guardrails",
        "blurb": "Platform rules the prompts cannot override",
        "cost": 12,
        "vector": (1, 0, 1),
    },
    "check_sender": {
        "name": "Verify Sender",
        "blurb": "Confirm who actually wrote the request",
        "cost": 38,
        "vector": (1, 1, 0),
    },
    # round 4 attack: knowledge base poisoning
    "poisoned_faq": {
        "name": "Poisoned FAQ",
        "blurb": "One plausible page saying the fee is zero",
        "cost": 38,
        "vector": (3, 1, 0),
    },
    "stale_version": {
        "name": "Stale Version Upload",
        "blurb": "The 2021 policy, filed as current",
        "cost": 18,
        "vector": (2, 0, 1),
    },
    "insider_contribution": {
        "name": "Insider Contribution",
        "blurb": "Someone with edit rights and a grudge",
        "cost": 26,
        "vector": (1, 2, 0),
    },
    "auto_ingest": {
        "name": "Auto-Ingest Crawler",
        "blurb": "Let the crawler pull in anything it likes",
        "cost": 44,
        "vector": (2, 0, 2),
    },
    "typosquat_source": {
        "name": "Typosquatted Source",
        "blurb": "A source that looks like the regulator",
        "cost": 12,
        "vector": (1, 0, 1),
    },
    "deepfake_memo": {
        "name": "Deepfake Memo",
        "blurb": "A signed memo nobody signed",
        "cost": 34,
        "vector": (0, 3, 0),
    },
    # round 4 defence: knowledge base poisoning
    "provenance_check": {
        "name": "Source Provenance Check",
        "blurb": "Every chunk traces back to an owner",
        "cost": 32,
        "vector": (3, 0, 1),
    },
    "editorial_review": {
        "name": "Human Editorial Review",
        "blurb": "A person approves what gets published",
        "cost": 20,
        "vector": (1, 2, 0),
    },
    "version_pinning": {
        "name": "Version Pinning",
        "blurb": "Only the signed revision is live",
        "cost": 30,
        "vector": (2, 0, 1),
    },
    "ingest_allowlist": {
        "name": "Ingest Allowlist",
        "blurb": "The crawler may only read listed domains",
        "cost": 34,
        "vector": (1, 0, 2),
    },
    "citation_required": {
        "name": "Citation Required",
        "blurb": "An answer without a source is not served",
        "cost": 20,
        "vector": (1, 0, 0),
    },
    "link_inspection": {
        "name": "Inspect Link",
        "blurb": "Check the URL before it is trusted",
        "cost": 38,
        "vector": (0, 0, 1),
    },
}

# --- rounds -------------------------------------------------------------------
# Attack and defence alternate sides so each team attacks exactly twice, which
# is what keeps the match symmetric: the attacker holds the one-point
# structural advantage, so an unbalanced schedule would decide the winner before
# anyone plays.
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
            "cloned_login_page",
            "malicious_attachment",
            "personalised_context",
            "spoofed_sender",
            "urgent_deadline",
            "voice_call_followup",
        ],
        "defence": [
            "email_gateway",
            "security_training",
            "verify_sender",
            "hover_before_click",
            "report_suspicious",
            "verify_payment",
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
            "deepfake_voice",
            "deepfake_video",
            "urgent_wire",
            "lookalike_domain",
            "authority_pressure",
            "malicious_login",
        ],
        "defence": [
            "payment_approval",
            "separate_channel",
            "verify_identity",
            "escalate_manager",
            "check_domain",
            "inspect_link",
        ],
    },
    {
        "round": 3,
        "theme": "AI assistant manipulation",
        "target": INFRA,
        "attacker": "red",
        "defender": "blue",
        "attack_brief": "Make the assistant do what its instructions forbid.",
        "defence_brief": "Keep the assistant inside its own rules.",
        "attack": [
            "prompt_injection",
            "hidden_instruction",
            "jailbreak",
            "data_exfiltration",
            "false_context",
            "fake_ceo_email",
        ],
        "defence": [
            "least_privilege",
            "input_sanitisation",
            "output_review",
            "human_in_the_loop",
            "model_guardrails",
            "check_sender",
        ],
    },
    {
        "round": 4,
        "theme": "Knowledge base poisoning",
        "target": DATA,
        "attacker": "blue",
        "defender": "red",
        "attack_brief": "Make the assistant tell a customer something false.",
        "defence_brief": "Prove every answer came from a trusted source.",
        "attack": [
            "poisoned_faq",
            "stale_version",
            "insider_contribution",
            "auto_ingest",
            "typosquat_source",
            "deepfake_memo",
        ],
        "defence": [
            "provenance_check",
            "editorial_review",
            "version_pinning",
            "ingest_allowlist",
            "citation_required",
            "link_inspection",
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
