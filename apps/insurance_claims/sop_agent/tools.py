"""
The business actions ("tools") and the phase gate.

The controller calls the tools; the LLM does not. TOOLS_BY_PHASE lists what each phase
may use, and ToolBox.call() raises ToolNotAllowed otherwise. So even a bug cannot list claims
during VERIFY_ID.

Mock services: consent returns the next status from consent_scenarios.json on each check;
the email is written to the outbox folder, and nothing is sent.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .data_store import DataStore
from .guidance import DocumentGuidance, GuidanceSnippet
from .models import Claim, Representative
from .state import Intent, Phase, SessionState
from .summary import EmailSummary, render_email_text
from .verification import VerificationResult, verify_identity

TOOLS_BY_PHASE: dict[Phase, set[str]] = {
    Phase.VERIFY_ID: {
        "verify_identity",
        "find_representative",
        "request_consent",
        "check_consent",
    },
    Phase.RESOLVE_INTENT: {
        "list_claims",
    },
    Phase.PROCESS_CASE: {
        "list_claims",
        "get_claim",
        "get_document_requirements",
        "get_document_alternatives",
        "get_followup_snippets",
    },
    Phase.POST_PROCESS: {
        "get_claim",
        "send_email_summary",
    },
    Phase.ENDED: set(),
}


class ToolNotAllowed(Exception):
    pass


class ToolBox:
    def __init__(self, store: DataStore, guidance: DocumentGuidance, settings: Settings) -> None:
        self.store = store
        self.guidance = guidance
        self.settings = settings
        self._registry: dict[str, Callable[..., Any]] = {
            "verify_identity": self.verify_identity,
            "find_representative": self.find_representative,
            "request_consent": self.request_consent,
            "check_consent": self.check_consent,
            "list_claims": self.list_claims,
            "get_claim": self.get_claim,
            "get_document_requirements": self.get_document_requirements,
            "get_document_alternatives": self.get_document_alternatives,
            "get_followup_snippets": self.get_followup_snippets,
            "send_email_summary": self.send_email_summary,
        }

    def call(self, name: str, state: SessionState, **kwargs: Any) -> Any:
        """Run one tool through the phase gate."""
        if name not in TOOLS_BY_PHASE.get(state.phase, set()):
            state.events.append(f"BLOCKED tool {name} in {state.phase.value}")
            raise ToolNotAllowed(f"Tool '{name}' is not allowed in phase {state.phase.value}")
        state.events.append(f"tool: {name}")
        return self._registry[name](state, **kwargs)

    def verify_identity(self, state: SessionState) -> VerificationResult:
        return verify_identity(
            state.identity,
            self.store,
            required=self.settings.required_identity_matches,
            refused=state.fields_refused,
        )

    def find_representative(self, state: SessionState, buyer_party_id: str) -> Representative | None:
        if not state.rep_name:
            return None
        return self.store.find_representative(state.rep_name, buyer_party_id)

    def _consent_sequence(self) -> list[str]:
        scenarios = self.store.consent_scenarios
        scenario = scenarios.get(self.settings.consent_scenario) or scenarios["default"]
        return scenario["status_sequence"]

    def request_consent(self, state: SessionState, buyer_party_id: str) -> str:
        """Mock: start a consent request to the customer."""
        state.consent_polls = 0
        state.consent_status = "pending"
        state.events.append(f"consent requested from {buyer_party_id} (scenario: {self.settings.consent_scenario})")
        return "pending"

    def check_consent(self, state: SessionState) -> str:
        """Mock: the next status from the scenario. After the end, keep the last status."""
        sequence = self._consent_sequence()
        status = sequence[min(state.consent_polls, len(sequence) - 1)]
        state.consent_polls += 1
        state.consent_status = status
        state.events.append(f"consent check {state.consent_polls}: {status}")
        return status

    def list_claims(self, state: SessionState) -> list[Claim]:
        """Only the verified customer's claims. Never uses a party_id from the caller message."""
        if not state.verified_party_id:
            return []
        return self.store.claims_for_party(state.verified_party_id)

    def get_claim(self, state: SessionState) -> Claim | None:
        if not (state.verified_party_id and state.selected_case_id):
            return None
        return self.store.get_claim(state.selected_case_id, state.verified_party_id)

    def get_document_requirements(self, state: SessionState) -> list[GuidanceSnippet]:
        claim = self.get_claim(state)
        if not claim or not claim.documents_needed:
            return []
        return self.guidance.requirements_for_claim(claim)

    def get_document_alternatives(self, state: SessionState, document_name: str) -> list[GuidanceSnippet]:
        return self.guidance.alternatives_for_document(document_name)

    def get_followup_snippets(
        self, state: SessionState, question: str | None, intent: Intent | None
    ) -> list[GuidanceSnippet]:
        claim = self.get_claim(state)
        if not claim:
            return []
        return self.guidance.followup_snippets(question, intent, claim)

    def send_email_summary(self, state: SessionState, summary: EmailSummary) -> Path:
        """Mock email: write it to the outbox. The address is always the one on file, never one from the chat."""
        outbox = self.settings.outbox_dir
        outbox.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = outbox / f"email-{stamp}.json"
        payload = {"to": summary.to_address, "subject": summary.subject, "body": render_email_text(summary)}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        state.email_sent = True
        state.sent_email = payload
        state.events.append(f"email written to {path.name}")
        return path
