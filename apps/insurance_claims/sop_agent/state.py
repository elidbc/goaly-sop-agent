"""
The session state: everything the agent knows about one conversation.

The code (not the LLM chat history) is the source of truth for the phase,
what the caller said, what we verified, and what we remember for later phases.
Memory is separate from the phase: a hint given "too early" (for example during VERIFY_ID)
is saved in `memory` at once, and a later phase uses it.
"""

from enum import Enum

from pydantic import BaseModel, Field


class Phase(str, Enum):
    """The SOP phases, in fixed order. Only the controller changes the phase."""

    VERIFY_ID = "VERIFY_ID"
    RESOLVE_INTENT = "RESOLVE_INTENT"
    PROCESS_CASE = "PROCESS_CASE"
    POST_PROCESS = "POST_PROCESS"
    ENDED = "ENDED"


class CallerRole(str, Enum):
    UNKNOWN = "unknown"
    POLICYHOLDER = "policyholder"
    REPRESENTATIVE = "representative"  # Calls for the customer (see representatives.json).


class Intent(str, Enum):
    """From the "intent_hints" in required_document_guideline.json."""

    STATUS_INQUIRY = "status_inquiry"
    DENIAL_QUESTION = "denial_question"
    DOCUMENT_SUBMISSION = "document_submission"
    NEXT_STEPS = "next_steps"
    GENERAL_CLAIM_QUESTION = "general_claim_question"


class IdentityClaims(BaseModel):
    """What the caller SAYS about the policyholder. Not verified."""

    full_name: str | None = None
    dob: str | None = None             # "YYYY-MM-DD"
    phone: str | None = None
    email: str | None = None
    id_last4: str | None = None        # SSN or national ID
    policy_number: str | None = None   # Not a counted identity field (Q1).


class CaseHints(BaseModel):
    """
    Clues about which claim the caller means. They accumulate over the conversation.
    Structured fields: the code filters the claims with them.
    Free-text fields: the LLM ranks the remaining claims with them (see case_resolver.py).
    """

    case_id: str | None = None
    case_type: str | None = None
    status: str | None = None
    date_from: str | None = None       # "January" -> a date range, relative to the demo "today".
    date_to: str | None = None
    excluded_case_ids: list[str] = []  # "Not that one."

    description: str | None = None     # For example "claim for a biopsy".
    raw_mentions: list[str] = []       # The caller's own words, from every turn.


class Memory(BaseModel):
    """Facts that the caller gives before we need them. Saved in any phase."""

    intent: Intent | None = None
    case_hints: CaseHints = Field(default_factory=CaseHints)
    notes: list[str] = []


class ChatTurn(BaseModel):
    role: str                          # "user" or "assistant"
    text: str


class SessionState(BaseModel):
    """All the data for one conversation."""

    phase: Phase = Phase.VERIFY_ID

    # VERIFY_ID
    caller_role: CallerRole = CallerRole.UNKNOWN
    identity: IdentityClaims = Field(default_factory=IdentityClaims)
    fields_refused: list[str] = []
    verified_party_id: str | None = None   # Set only by the controller.
    # Representative flow: the customer matched, but consent is not approved yet.
    pending_party_id: str | None = None
    rep_name: str | None = None
    rep_relationship: str | None = None
    consent_status: str | None = None  # "pending", "approved", "timed_out"
    consent_polls: int = 0

    memory: Memory = Field(default_factory=Memory)

    # RESOLVE_INTENT and PROCESS_CASE
    selected_case_id: str | None = None
    pending_case_id: str | None = None # Waits for the caller to confirm it.
    candidate_case_ids: list[str] = []
    case_intro_done: bool = False
    missing_documents: list[str] = []

    # Scope guard
    off_topic_strikes: int = 0
    human_suggested: bool = False

    # POST_PROCESS
    topics_discussed: list[str] = []
    next_steps: list[str] = []
    email_offered: bool = False
    email_sent: bool = False
    sent_email: dict | None = None

    history: list[ChatTurn] = []
    events: list[str] = []             # Controller decisions, for the debug panel.

    def is_verified(self) -> bool:
        return self.verified_party_id is not None
