"""Sabotage configuration: cards, matrix, scenarios and timings.

Everything here is data, so tuning the game never means touching the engine.
The single-use Attack deck is shared across both teams; the Defence deck is
reusable every defence round. Costs, rewards and penalties follow the
developer storyboard (design/AI_Cyber_Sabotage_Developer_Storyboard.pdf).
"""

from __future__ import annotations

# Budget and payout are in whole dollars; points are small integers.
STARTING_BUDGET = 100_000
WINNER_BONUS = 100

# What a room with a single player is paid when Sabotage is skipped: two teams
# cannot be fielded with one person, so the section would otherwise be a zero
# on the leaderboard. This is a *perfect* win - the best reachable team total
# (2_600, found by exhausting the matrix under the shared deck and budget) plus
# the bonus. Re-derive it whenever cards, costs, the matrix or the budget move.
SOLO_SCORE = 2_700

# Every attack card is single-use and shared: once either team plays it, the
# card is SPENT for the rest of the show.
ATTACK_CARDS: dict[str, dict] = {
    "deepfake_video": {
        "name": "Deepfake Video",
        "description": "Convincing fake video of someone the victim trusts",
        "cost": 30_000,
        "success": 900,
        "fail": -350,
    },
    "cloned_voice": {
        "name": "Cloned Voice",
        "description": "Copy someone's voice for a fake call or message",
        "cost": 25_000,
        "success": 750,
        "fail": -300,
    },
    "personalised_message": {
        "name": "Personalised Message",
        "description": "Use personal context to make a message believable",
        "cost": 20_000,
        "success": 650,
        "fail": -250,
    },
    "ai_written_message": {
        "name": "AI-Written Message",
        "description": "Make a scam message polished and convincing",
        "cost": 10_000,
        "success": 300,
        "fail": -100,
    },
    "fake_digital_identity": {
        "name": "Fake Digital Identity",
        "description": "Create a convincing fake profile or persona",
        "cost": 20_000,
        "success": 600,
        "fail": -250,
    },
    "ai_agent_attack": {
        "name": "AI Agent Attack",
        "description": "Automate actions and adapt the attack",
        "cost": 35_000,
        "success": 1_000,
        "fail": -400,
    },
}

# The defence deck reappears every defence round; only affordability disables a
# card, never use.
DEFENCE_CARDS: dict[str, dict] = {
    "stop_the_action": {
        "name": "Stop the Action",
        "description": "Pause the request or action before anything happens",
        "cost": 20_000,
        "success": 750,
        "fail": -150,
    },
    "verify_independently": {
        "name": "Verify Independently",
        "description": "Confirm through a trusted source or channel",
        "cost": 18_000,
        "success": 700,
        "fail": -150,
    },
    "report_and_escalate": {
        "name": "Report & Escalate",
        "description": "Flag suspicious activity through the right process",
        "cost": 15_000,
        "success": 600,
        "fail": -175,
    },
    "investigate_further": {
        "name": "Investigate Further",
        "description": "Inspect content for signs something is wrong",
        "cost": 8_000,
        "success": 300,
        "fail": -275,
    },
    "ask_someone_else": {
        "name": "Ask Someone Else",
        "description": "Get a colleague's opinion before deciding",
        "cost": 5_000,
        "success": 150,
        "fail": -350,
    },
    "continue_carefully": {
        "name": "Continue Carefully",
        "description": "Proceed but avoid sharing anything sensitive",
        "cost": 0,
        "success": 50,
        "fail": -500,
    },
}

# attack x defence -> outcome. "blocked" credits the defender's success and
# nothing to the attacker; "failed" credits the attacker's success and the
# defender's fail; "partial" credits both sides zero. Every attack has at
# least one blocking defence, and Continue Carefully never blocks anything.
EFFECT_MATRIX: dict[str, dict[str, str]] = {
    "deepfake_video": {
        "stop_the_action": "partial",
        "verify_independently": "blocked",
        "report_and_escalate": "partial",
        "investigate_further": "blocked",
        "ask_someone_else": "failed",
        "continue_carefully": "failed",
    },
    "cloned_voice": {
        "stop_the_action": "partial",
        "verify_independently": "blocked",
        "report_and_escalate": "partial",
        "investigate_further": "partial",
        "ask_someone_else": "partial",
        "continue_carefully": "failed",
    },
    "personalised_message": {
        "stop_the_action": "failed",
        "verify_independently": "partial",
        "report_and_escalate": "partial",
        "investigate_further": "blocked",
        "ask_someone_else": "failed",
        "continue_carefully": "failed",
    },
    "ai_written_message": {
        "stop_the_action": "partial",
        "verify_independently": "failed",
        "report_and_escalate": "blocked",
        "investigate_further": "partial",
        "ask_someone_else": "partial",
        "continue_carefully": "failed",
    },
    "fake_digital_identity": {
        "stop_the_action": "partial",
        "verify_independently": "blocked",
        "report_and_escalate": "failed",
        "investigate_further": "blocked",
        "ask_someone_else": "failed",
        "continue_carefully": "failed",
    },
    "ai_agent_attack": {
        "stop_the_action": "blocked",
        "verify_independently": "partial",
        "report_and_escalate": "partial",
        "investigate_further": "failed",
        "ask_someone_else": "failed",
        "continue_carefully": "failed",
    },
}

# Three rounds. Red attacks twice, Blue once; the defender answers each time.
SCENARIOS: list[dict] = [
    {
        "attacker": "red",
        "defender": "blue",
        "attack_title": "Personalised phishing",
        "attack_prompt": (
            "Make a phishing email convincing enough to drive an employee to "
            "a fraudulent login page."
        ),
        "defence_title": "ATTACK INCOMING",
        "defence_prompt": (
            "Email knows your name, role, manager and project. Urgent login "
            "requested."
        ),
        "learning": "Familiar details do not make an email real.",
    },
    {
        "attacker": "blue",
        "defender": "red",
        "attack_title": "CEO impersonation",
        "attack_prompt": (
            "Convince Finance that the CEO urgently needs an unusual payment "
            "approved."
        ),
        "defence_title": "ATTACK INCOMING",
        "defence_prompt": (
            "Voice, video or message appears to come from the CEO. Urgent "
            "unusual payment request."
        ),
        "learning": "A familiar voice or face is not proof of identity.",
    },
    {
        "attacker": "red",
        "defender": "blue",
        "attack_title": "AI assistant manipulation",
        "attack_prompt": (
            "Manipulate an AI assistant into taking an unauthorised action."
        ),
        "defence_title": "AI ASSISTANT UNDER ATTACK",
        "defence_prompt": (
            "Malicious instructions hidden in content try to trigger an "
            "unauthorised action."
        ),
        "learning": "Instructions hidden in content are still instructions.",
    },
]

# Seconds per phase. The total is roughly the 2.5-minute slot reserved in the
# flow for this node; adjust here, not in the engine.
TIMINGS: dict[str, int] = {
    "team_reveal": 8,
    "attack": 20,
    "defend": 20,
    "tie_break": 5,
    "resolve": 4,
    "team_result": 6,
    "payout": 5,
}
