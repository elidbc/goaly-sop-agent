"""
Out-of-scope policy (README): decline politely; if the caller keeps trying, suggest a human
representative. That is only a sentence in the reply: no transfer, no phase change.

The counter resets after an in-scope message, so the limit counts off-topic messages in a row.
A mixed message ("My DOB is ... Also, what is RL?") is in scope: the facts are saved,
the off-topic part is declined, and it is not a strike.
"""

from enum import Enum

from .extraction import ExtractedFacts
from .state import SessionState


class ScopeDecision(str, Enum):
    ALLOW = "allow"
    DECLINE = "decline"              # Decline, then continue the current step.
    SUGGEST_HUMAN = "suggest_human"  # Decline and suggest a human representative.


def check_scope(facts: ExtractedFacts, state: SessionState, max_strikes: int) -> ScopeDecision:
    if facts.in_scope:
        if state.off_topic_strikes:
            state.events.append("scope: in-scope message, off-topic counter reset")
        state.off_topic_strikes = 0
        return ScopeDecision.DECLINE if facts.has_off_topic_part else ScopeDecision.ALLOW

    state.off_topic_strikes += 1
    state.events.append(f"scope: off-topic message ({state.off_topic_strikes}/{max_strikes})")
    if state.off_topic_strikes >= max_strikes:
        state.human_suggested = True
        return ScopeDecision.SUGGEST_HUMAN
    return ScopeDecision.DECLINE
