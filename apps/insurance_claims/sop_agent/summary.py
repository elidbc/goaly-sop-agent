"""
The POST_PROCESS email summary: what was discussed, the claim status, and the next steps.
The content comes from what the code recorded during the call, so it stays grounded.
Plain template, no LLM.
"""

from pydantic import BaseModel

from .data_store import DataStore
from .state import SessionState


class EmailSummary(BaseModel):
    to_address: str            # Always the email on file.
    customer_name: str
    subject: str
    discussed: list[str]
    claim_status: str
    next_steps: list[str]


def claim_status_line(claim) -> str:
    line = f"{claim.case_id} ({claim.case_type}, filed {claim.created_at}): {claim.status}. {claim.summary}."
    if claim.denial_reason:
        line += f" Denial reason: {claim.denial_reason}."
    if claim.appeal_deadline:
        line += f" Appeal deadline: {claim.appeal_deadline}."
    return line


def build_summary(state: SessionState, store: DataStore) -> EmailSummary:
    customer = store.get_policyholder(state.verified_party_id)
    claim = store.get_claim(state.selected_case_id, state.verified_party_id) if state.selected_case_id else None
    status = claim_status_line(claim) if claim else "No specific claim was discussed."
    subject = f"Summary of your call about claim {claim.case_id}" if claim else "Summary of your call"
    return EmailSummary(
        to_address=customer.email,
        customer_name=customer.name,
        subject=subject,
        discussed=list(state.topics_discussed) or ["General questions about your claims."],
        claim_status=status,
        next_steps=list(state.next_steps) or ["No follow-up action is needed at this time."],
    )


def render_email_text(summary: EmailSummary) -> str:
    lines = [f"Dear {summary.customer_name},", "", "Thank you for your call. Here is a summary.", ""]
    lines.append("What we discussed:")
    lines += [f"  - {item}" for item in summary.discussed]
    lines += ["", "Claim status:", f"  {summary.claim_status}", "", "Next steps:"]
    lines += [f"  - {item}" for item in summary.next_steps]
    lines += ["", "Kind regards,", "Claims Support"]
    return "\n".join(lines)
