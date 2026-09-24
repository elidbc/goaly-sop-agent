"""
Last safety check on the reply (regex, no LLM). A second line of defense: by design the responder
has no claim data before verification, but a bug could put data in the wrong place.
Before verification: no claim ID that the caller did not say first, and no money amounts.
After verification: no claim ID of another customer. A failed check replaces the reply with a safe text.
"""

import re

from pydantic import BaseModel

from .data_store import DataStore
from .state import SessionState

SAFE_FALLBACK = (
    "Before I can discuss any account details, I need to verify your identity. "
    "Could you please share your full name, date of birth, and one more detail, "
    "such as your phone number, email, or the last 4 digits of your SSN?"
)
SAFE_FALLBACK_VERIFIED = "Sorry, I made a mistake in my last answer. Could you please repeat your question?"

CLAIM_ID = re.compile(r"\bCL-\d+\b", re.IGNORECASE)
MONEY = re.compile(r"\$\s?\d|\b\d+\.\d{2}\b|\bUSD\b")


class GuardResult(BaseModel):
    ok: bool
    reply: str
    violations: list[str] = []


def check_reply(reply: str, state: SessionState, store: DataStore) -> GuardResult:
    violations = []
    said_by_caller = {m.upper() for t in state.history if t.role == "user" for m in CLAIM_ID.findall(t.text)}
    ids_in_reply = {m.upper() for m in CLAIM_ID.findall(reply)}

    if not state.is_verified():
        if ids_in_reply - said_by_caller:
            violations.append("claim_id_before_verification")
        if MONEY.search(reply):
            violations.append("amount_before_verification")
        fallback = SAFE_FALLBACK
    else:
        own = {c.case_id for c in store.claims_for_party(state.verified_party_id)}
        if ids_in_reply - own:
            violations.append("claim_id_of_other_customer")
        fallback = SAFE_FALLBACK_VERIFIED

    if violations:
        state.events.append(f"OUTPUT GUARD blocked the reply: {violations}")
        return GuardResult(ok=False, reply=fallback, violations=violations)
    return GuardResult(ok=True, reply=reply)
