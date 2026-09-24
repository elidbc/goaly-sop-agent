"""
Records of the mock company database (the JSON fixtures), checked by Pydantic at start-up.
These are the TRUTH from the company systems. What the caller says is in state.IdentityClaims.
"""

from pydantic import BaseModel


class Policyholder(BaseModel):
    """One customer. Source: fixtures/policyholders.json."""

    party_id: str
    name: str
    policy_number: str
    dob: str
    id_type: str                  # "ssn_last4" or "national_id_last4"
    id_last4: str
    phone: str
    email: str
    # Other accepted values (for example speech-to-text spellings).
    name_aliases: list[str] = []
    phone_aliases: list[str] = []
    email_aliases: list[str] = []


class Claim(BaseModel):
    """One insurance claim. Source: fixtures/claims.json."""

    case_id: str
    party_id: str
    case_type: str
    created_at: str
    status: str
    summary: str
    # Only on denied claims:
    denial_reason: str | None = None
    documents_needed: list[str] = []
    appeal_deadline: str | None = None
    # USD strings. Meanings: fixtures/claim_schema.json.
    expected_reimbursement_amount: str
    allowed_max_amount: str
    net_pay: str
    net_fee: str


class Representative(BaseModel):
    """A person who can call for a customer. Source: fixtures/representatives.json."""

    rep_name: str
    relationship: str
    buyer_name: str
    buyer_party_id: str
