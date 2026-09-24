"""
Step 1 of each turn: turn the caller message into structured facts.

It runs on every turn, in every phase, and extracts facts for ALL phases.
This is how the agent remembers information whenever the caller says it.
The LLM only reports what the caller said. The controller makes all decisions.
The LLM gets no database data, so it cannot leak claim data or "verify" anybody.

Two schemas: the LLM fills the flat ExtractionOutput (every field required, no nulls),
because the API rejects a structured-output schema with many nullable fields as "too complex".
to_facts() converts it into ExtractedFacts, which the rest of the code uses.
"""

from typing import Literal

from pydantic import BaseModel, Field

from .llm_client import LLMClient
from .prompts import EXTRACTION_PROMPT
from .state import CallerRole, CaseHints, IdentityClaims, Intent, SessionState

HISTORY_TURNS = 6  # Earlier messages given as context.


class ExtractedFacts(BaseModel):
    """The facts in one caller message. None or empty = not said."""

    identity: IdentityClaims = Field(default_factory=IdentityClaims)  # Always the policyholder's details.
    fields_refused: list[str] = []
    caller_role: CallerRole | None = None
    rep_name: str | None = None
    rep_relationship: str | None = None

    intent: Intent | None = None
    case_hints: CaseHints = Field(default_factory=CaseHints)
    question: str | None = None
    missing_documents: list[str] = []

    in_scope: bool = True
    has_off_topic_part: bool = False   # In scope, but also asks something unrelated.
    wants_to_end: bool = False
    confirms: bool | None = None       # Answer to the agent's last yes/no question.
    email_choice: bool | None = None
    notes: list[str] = []


class ExtractionOutput(BaseModel):
    """The flat schema that the LLM fills. Empty = "", [], or "not_said". The LLM reads the descriptions."""

    # Identity of the POLICYHOLDER (also when a representative calls).
    full_name: str = Field(description="Policyholder full name, or ''.")
    dob: str = Field(description="Policyholder date of birth as YYYY-MM-DD, or ''.")
    phone: str = Field(description="Policyholder phone number as said, or ''.")
    email: str = Field(description="Policyholder email, or ''.")
    id_last4: str = Field(description="Last 4 digits of SSN or national ID, or ''.")
    policy_number: str = Field(description="Policy number, or ''.")
    fields_refused: list[str] = Field(
        description="Identity fields the caller refuses to give: full_name, dob, phone, email, id_last4."
    )
    caller_role: Literal["policyholder", "representative", "not_said"] = Field(
        description="'policyholder' if the caller is the insured person, 'representative' if they call for somebody else."
    )
    rep_name: str = Field(description="If a representative calls: their own full name, or ''.")
    rep_relationship: str = Field(description="If a representative calls: the relationship, e.g. 'son', or ''.")

    # Intent and claim clues.
    intent: Literal[
        "status_inquiry", "denial_question", "document_submission", "next_steps", "general_claim_question", "not_said"
    ] = Field(description="What the caller wants.")
    case_id: str = Field(description="Claim ID like CL-2048, or ''.")
    case_type: Literal["healthcare", "dental", "auto", "not_said"] = Field(description="Claim type.")
    status: Literal["denied", "open", "closed", "not_said"] = Field(description="Claim status the caller mentions.")
    date_from: str = Field(description="Start of the claim date range as YYYY-MM-DD, or ''.")
    date_to: str = Field(description="End of the claim date range as YYYY-MM-DD, or ''.")
    excluded_case_ids: list[str] = Field(description="Claim IDs the caller says are NOT the one they mean.")
    claim_description: str = Field(description="Other clues about which claim, in a few words, or ''.")
    raw_mentions: list[str] = Field(description="The caller's own words about the claim, verbatim.")
    question: str = Field(description="The caller's question about their claim, as one short sentence, or ''.")
    missing_documents: list[str] = Field(description="Documents the caller says they do not have or cannot get.")

    # Control signals.
    in_scope: bool = Field(description="False only if the WHOLE message is unrelated to insurance, claims, or this call.")
    has_off_topic_part: bool = Field(description="True if an in-scope message ALSO has an unrelated question.")
    wants_to_end: bool = Field(description="True if the caller has no more questions or says goodbye.")
    confirms: Literal["yes", "no", "not_said"] = Field(description="Answer to the agent's last yes/no question.")
    email_choice: Literal["send", "skip", "not_said"] = Field(
        description="Only if the agent offered an email summary: the caller's choice."
    )
    notes: list[str] = Field(description="Other useful facts in this message.")

    def to_facts(self) -> ExtractedFacts:
        """Convert the flat LLM output into the internal ExtractedFacts ("" / "not_said" -> None)."""

        def text(value: str) -> str | None:
            return value.strip() or None

        def choice(value: str) -> str | None:
            return None if value == "not_said" else value

        yes_no = {"yes": True, "no": False, "send": True, "skip": False}
        return ExtractedFacts(
            identity=IdentityClaims(
                full_name=text(self.full_name),
                dob=text(self.dob),
                phone=text(self.phone),
                email=text(self.email),
                id_last4=text(self.id_last4),
                policy_number=text(self.policy_number),
            ),
            fields_refused=self.fields_refused,
            caller_role=CallerRole(self.caller_role) if choice(self.caller_role) else None,
            rep_name=text(self.rep_name),
            rep_relationship=text(self.rep_relationship),
            intent=Intent(self.intent) if choice(self.intent) else None,
            case_hints=CaseHints(
                case_id=text(self.case_id),
                case_type=choice(self.case_type),
                status=choice(self.status),
                date_from=text(self.date_from),
                date_to=text(self.date_to),
                excluded_case_ids=self.excluded_case_ids,
                description=text(self.claim_description),
                raw_mentions=self.raw_mentions,
            ),
            question=text(self.question),
            missing_documents=self.missing_documents,
            in_scope=self.in_scope,
            has_off_topic_part=self.has_off_topic_part,
            wants_to_end=self.wants_to_end,
            confirms=yes_no.get(self.confirms),
            email_choice=yes_no.get(self.email_choice),
            notes=self.notes,
        )


def history_as_messages(state: SessionState, max_turns: int) -> list[dict]:
    """The last turns as API messages. The first must be from the user, and the roles must alternate."""
    turns = state.history[-max_turns:]
    while turns and turns[0].role != "user":
        turns = turns[1:]
    messages: list[dict] = []
    for turn in turns:
        if messages and messages[-1]["role"] == turn.role:
            messages[-1]["content"] += "\n" + turn.text
        else:
            messages.append({"role": turn.role, "content": turn.text})
    return messages


def last_agent_message(state: SessionState) -> str:
    for turn in reversed(state.history):
        if turn.role == "assistant":
            return turn.text
    return "(none)"


def build_extraction_prompt(state: SessionState, today: str) -> str:
    return EXTRACTION_PROMPT.format(
        phase=state.phase.value,
        today=today,
        last_agent_message=last_agent_message(state),
    )


def extract_facts(message: str, state: SessionState, llm: LLMClient, today: str) -> ExtractedFacts:
    """
    Extract the facts from the last caller message. `today` lets the LLM turn "January" into dates.
    Raises LLMError on failure. The agent then asks the caller to repeat and changes nothing.
    """
    system = build_extraction_prompt(state, today)
    messages = history_as_messages(state, HISTORY_TURNS)
    if not messages or messages[-1]["role"] != "user":
        messages.append({"role": "user", "content": message})
    messages[-1] = {
        "role": "user",
        "content": f"{messages[-1]['content']}\n\n(Extract the facts from this last caller message only.)",
    }
    return llm.extract(system, messages, ExtractionOutput).to_facts()
