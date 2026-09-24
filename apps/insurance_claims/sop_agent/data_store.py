"""
The mock company database.


    This module reads the JSON files in the fixtures folder.
    Only the tools module and the verification module use the DataStore.
    The LLM NEVER gets direct access to the DataStore.
    This rule is the base of our safety design: the LLM sees only data that the SOP releases.

HOW IT WORKS:
    The DataStore loads all the files one time at start-up.
    Then it gives simple look-up functions.
    It does not apply business rules. The business rules are in the controller.
"""

import json
from pathlib import Path

from .models import Claim, Policyholder, Representative
from .normalize import normalize_name


def _read_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


class DataStore:
    """Read-only access to the fixture data."""

    def __init__(self, fixtures_dir: Path) -> None:
        """
        Load all the fixture files.
        Pydantic checks each record. Bad fixture data fails here, at start-up.
        """
        self.policyholders: list[Policyholder] = [
            Policyholder(**row) for row in _read_json(fixtures_dir / "policyholders.json")
        ]
        self.claims: list[Claim] = [Claim(**row) for row in _read_json(fixtures_dir / "claims.json")]
        self.representatives: list[Representative] = [
            Representative(**row) for row in _read_json(fixtures_dir / "representatives.json")
        ]
        # These three stay dictionaries. Other modules read them.
        self.document_guidelines: dict = _read_json(fixtures_dir / "required_document_guideline.json")
        self.consent_scenarios: dict = _read_json(fixtures_dir / "consent_scenarios.json")
        self.claim_schema: dict = _read_json(fixtures_dir / "claim_schema.json")

    # --- Customers ---

    def get_policyholder(self, party_id: str) -> Policyholder | None:
        """Return the customer with this party_id, or None."""
        return next((p for p in self.policyholders if p.party_id == party_id), None)

    def all_policyholders(self) -> list[Policyholder]:
        """Return all the customers. The verification module uses this to find candidate records."""
        return list(self.policyholders)

    # --- Claims ---

    def claims_for_party(self, party_id: str) -> list[Claim]:
        """Return all the claims that belong to one customer, the newest first."""
        own = [c for c in self.claims if c.party_id == party_id]
        return sorted(own, key=lambda c: c.created_at, reverse=True)

    def get_claim(self, case_id: str, party_id: str) -> Claim | None:
        """
        Return one claim.

        Ensures that the claim belongs to the verified customer.
        If the claim belongs to a different customer, return None.
        """
        wanted = case_id.strip().upper()
        return next((c for c in self.claims if c.case_id == wanted and c.party_id == party_id), None)

    # --- Representatives ---

    def find_representative(self, rep_name: str, buyer_party_id: str) -> Representative | None:
        """Return the representative record if this person can call for this customer."""
        wanted = normalize_name(rep_name)
        return next(
            (
                r
                for r in self.representatives
                if r.buyer_party_id == buyer_party_id and normalize_name(r.rep_name) == wanted
            ),
            None,
        )
