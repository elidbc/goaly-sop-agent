"""
Identity verification. Plain code, no LLM: an LLM can be persuaded, code cannot.

VERIFIED  - exactly one record matches >= 3 of: full name, DOB, phone, email, ID last 4 (aliases count).
NEED_MORE - not enough matches yet, but the caller can still give more fields (Q2: ask for one more).
MISMATCH  - even with every remaining field, no record can reach 3. Ask the caller to check the details.
The result never tells the caller WHICH field is wrong.
"""

from enum import Enum

from pydantic import BaseModel

from .data_store import DataStore
from .models import Policyholder
from .normalize import normalize_dob, normalize_email, normalize_last4, normalize_name, normalize_phone
from .state import IdentityClaims

# policy_number does not count (Q1).
COUNTED_FIELDS = ["full_name", "dob", "phone", "email", "id_last4"]


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    NEED_MORE = "need_more"
    MISMATCH = "mismatch"


class VerificationResult(BaseModel):
    status: VerificationStatus
    party_id: str | None = None        # Only when VERIFIED.
    matched_count: int = 0
    still_needed: int = 0
    matched_fields: list[str] = []     # Debug panel only. Never send to the responder.


def _field_matches(field: str, said: str, record: Policyholder) -> bool:
    if field == "full_name":
        return normalize_name(said) in {normalize_name(n) for n in [record.name, *record.name_aliases]}
    if field == "dob":
        return normalize_dob(said) == record.dob
    if field == "phone":
        return normalize_phone(said) in {normalize_phone(p) for p in [record.phone, *record.phone_aliases]}
    if field == "email":
        return normalize_email(said) in {normalize_email(e) for e in [record.email, *record.email_aliases]}
    if field == "id_last4":
        return len(normalize_last4(said)) == 4 and normalize_last4(said) == record.id_last4
    return False


def matched_fields(claims: IdentityClaims, record: Policyholder) -> list[str]:
    """The counted fields that the caller gave and that match this record."""
    result = []
    for field in COUNTED_FIELDS:
        said = getattr(claims, field)
        if said and _field_matches(field, said, record):
            result.append(field)
    return result


def verify_identity(
    claims: IdentityClaims,
    store: DataStore,
    required: int = 3,
    refused: list[str] | None = None,
) -> VerificationResult:
    """Check what the caller said against every policyholder record. Refused fields do not count as available."""
    refused = refused or []

    scored = [(matched_fields(claims, record), record) for record in store.all_policyholders()]
    best_count = max((len(fields) for fields, _ in scored), default=0)
    best = [(fields, record) for fields, record in scored if len(fields) == best_count]

    if best_count >= required and len(best) == 1:
        fields, record = best[0]
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            party_id=record.party_id,
            matched_count=best_count,
            matched_fields=fields,
        )

    given = [f for f in COUNTED_FIELDS if getattr(claims, f)]
    still_available = [f for f in COUNTED_FIELDS if f not in given and f not in refused]
    still_needed = max(1, required - best_count)
    debug_fields = best[0][0] if len(best) == 1 else []

    if len(still_available) < still_needed:
        return VerificationResult(
            status=VerificationStatus.MISMATCH,
            matched_count=best_count,
            still_needed=still_needed,
            matched_fields=debug_fields,
        )
    return VerificationResult(
        status=VerificationStatus.NEED_MORE,
        matched_count=best_count,
        still_needed=still_needed,
        matched_fields=debug_fields,
    )
