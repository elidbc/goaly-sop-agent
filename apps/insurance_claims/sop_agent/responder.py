"""
Step 3 of each turn: write the reply. The controller already decided WHAT to do (the TurnPlan);
the responder decides HOW to say it. It gets data only from plan.facts, never the DataStore
or the full state, so it cannot leak claim data before verification.
"""

import json

from .controller import TurnPlan
from .extraction import history_as_messages
from .llm_client import LLMClient
from .prompts import PERSONA, PHASE_INSTRUCTIONS, SUGGEST_HUMAN_INSTRUCTION
from .state import SessionState

HISTORY_TURNS = 10


def build_system_prompt(plan: TurnPlan) -> str:
    parts = [PERSONA, PHASE_INSTRUCTIONS[plan.phase], f"Your goal for this reply: {plan.goal}"]
    if plan.constraints:
        parts.append("Rules for this reply:\n" + "\n".join(f"- {c}" for c in plan.constraints))
    if plan.decline_off_topic:
        parts.append(
            "The caller's last message contains a question that is not about insurance support. "
            "Politely decline that part in one short sentence, then continue with the goal."
        )
    if plan.suggest_human:
        parts.append(SUGGEST_HUMAN_INSTRUCTION)
    parts.append("<facts>\n" + json.dumps(plan.facts, indent=1, default=str) + "\n</facts>")
    return "\n\n".join(parts)


def write_reply(plan: TurnPlan, state: SessionState, llm: LLMClient) -> str:
    """`state` is used ONLY for the chat history. Do not copy other state fields into the prompt."""
    messages = history_as_messages(state, HISTORY_TURNS)
    return llm.generate(build_system_prompt(plan), messages)
