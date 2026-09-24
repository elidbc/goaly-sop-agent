"""
The SOP controller: the state machine that owns the workflow.

WHERE THIS FITS:
    This is the core of the harness. It is almost entirely pure 
    deterministicPython code (with one small LLM use).
    Each turn, it gets the ExtractedFacts, updates the SessionState, and decides
    if the phase changes, which tools run, and what the reply should do.
    The responder LLM then writes the words for the TurnPlan.
"""

from pydantic import BaseModel, Field

from .case_resolver import ResolutionStatus, resolve_case
from .extraction import ExtractedFacts
from .llm_client import LLMClient
from .models import Claim
from .scope_guard import ScopeDecision, check_scope
from .state import CallerRole, CaseHints, IdentityClaims, Phase, SessionState
from .summary import build_summary, claim_status_line
from .tools import ToolBox
from .verification import COUNTED_FIELDS, VerificationStatus

MAX_STEPS_PER_TURN = 5

# Disclaimer prepended to replies before verification.
NO_ACCOUNT_DATA = (
    "The caller is NOT verified. Do not mention or confirm any claim, account, or policy detail. "
    "You may repeat only what the caller said."
)

FIELD_LABELS = {
    "full_name": "full name",
    "dob": "date of birth",
    "phone": "phone number",
    "email": "email address",
    "id_last4": "last 4 digits of the SSN or national ID",
}


class TurnPlan(BaseModel):
    """
    The instructions for the responder. One TurnPlan is delivered per turn.

    This object is the ONLY channel from the controller to the responder LLM.
    If data is not in `facts`, the responder cannot say it.
    """

    phase: Phase
    # What the reply must do. Example: "Ask for one more identity detail."
    goal: str
    facts: dict = Field(default_factory=dict)
    constraints: list[str] = []
    decline_off_topic: bool = False
    suggest_human: bool = False


def claim_brief(claim: Claim) -> dict:
    """A short description of a claim, for lists ("which claim do you mean?")."""
    return {
        "case_id": claim.case_id,
        "case_type": claim.case_type,
        "status": claim.status,
        "filed_on": claim.created_at,
        "summary": claim.summary,
    }


def hints_conflict_with(hints: CaseHints, claim: Claim) -> bool:
    """True if the structured hints clearly point to a different claim."""
    if hints.case_id and hints.case_id.strip().upper() != claim.case_id:
        return True
    if hints.case_type and hints.case_type.lower() != claim.case_type:
        return True
    if hints.status and hints.status.lower() != claim.status:
        return True
    if hints.date_from and claim.created_at < hints.date_from:
        return True
    if hints.date_to and claim.created_at > hints.date_to:
        return True
    return False


def has_hints(hints: CaseHints) -> bool:
    return any([hints.case_id, hints.case_type, hints.status, hints.date_from, hints.date_to, hints.description])


class SopController:
    """Runs the SOP rules. One instance for the app. The state is unique to a conversation."""

    def __init__(self, tools: ToolBox, llm: LLMClient) -> None:
        # The controller uses the LLM in ONE place only: case_resolver.rank_with_llm()
        # in RESOLVE_INTENT. All other decisions in this class are plain code.
        self.tools = tools
        self.llm = llm
        self.settings = tools.settings
        self.steps = {
            Phase.VERIFY_ID: self.step_verify_id,
            Phase.RESOLVE_INTENT: self.step_resolve_intent,
            Phase.PROCESS_CASE: self.step_process_case,
            Phase.POST_PROCESS: self.step_post_process,
            Phase.ENDED: self.step_ended,
        }

    # --- Entry point ---

    def advance(self, state: SessionState, facts: ExtractedFacts) -> TurnPlan:
        """Process one turn. Change `state`. Return the TurnPlan for the reply."""
        self.merge_facts(state, facts)
        scope = check_scope(facts, state, self.settings.max_off_topic_strikes)

        plan = None
        for _ in range(MAX_STEPS_PER_TURN):
            plan = self.steps[state.phase](state, facts)
            if plan is not None:
                break
        if plan is None:  # Should not happen. A safe answer if it does.
            plan = TurnPlan(phase=state.phase, goal="Ask the caller how you can help with their claim.")

        plan.decline_off_topic = scope in (ScopeDecision.DECLINE, ScopeDecision.SUGGEST_HUMAN)
        plan.suggest_human = scope == ScopeDecision.SUGGEST_HUMAN
        if not state.is_verified() and NO_ACCOUNT_DATA not in plan.constraints:
            plan.constraints.append(NO_ACCOUNT_DATA)
        return plan

    def goto(self, state: SessionState, phase: Phase, reason: str) -> None:
        """Change the phase and record why."""
        state.events.append(f"phase: {state.phase.value} -> {phase.value} ({reason})")
        state.phase = phase

    def merge_facts(self, state: SessionState, facts: ExtractedFacts) -> None:
        """
        Save the facts into the state.

        RULES:
            - Identity fields: fill empty fields. If the caller corrects a field, replace it.
              After verification, identity does not change any more.
            - Memory (intent, case_hints, notes): save it in ANY phase. Do NOT change the phase here.
              Case hints ACCUMULATE: a new value fills or replaces a field, raw_mentions are appended.
              Exception: after a claim is selected, hints that point to a DIFFERENT claim replace
              all the old hints (the caller changes the subject; the old hints must not mix in).
            - Do not overwrite a known value with None.
        """
        if not state.is_verified():
            for field in IdentityClaims.model_fields:
                new = getattr(facts.identity, field)
                if new and new != getattr(state.identity, field):
                    action = "updated" if getattr(state.identity, field) else "received"
                    setattr(state.identity, field, new)
                    state.events.append(f"identity: {field} {action}")
                    if field in state.fields_refused:
                        state.fields_refused.remove(field)
            for field in facts.fields_refused:
                if field in COUNTED_FIELDS and field not in state.fields_refused and not getattr(state.identity, field):
                    state.fields_refused.append(field)
                    state.events.append(f"identity: caller refuses {field}")
            if facts.caller_role and facts.caller_role != CallerRole.UNKNOWN:
                state.caller_role = facts.caller_role
            state.rep_name = facts.rep_name or state.rep_name
            state.rep_relationship = facts.rep_relationship or state.rep_relationship

        memory = state.memory
        if facts.intent:
            memory.intent = facts.intent
        new_hints = facts.case_hints
        selected = self._selected_claim(state)
        if selected and has_hints(new_hints) and hints_conflict_with(new_hints, selected):
            memory.case_hints = new_hints.model_copy(deep=True)
            state.events.append("memory: caller mentions a different claim, hints replaced")
        else:
            hints = memory.case_hints
            for field in ["case_id", "case_type", "status", "date_from", "date_to", "description"]:
                value = getattr(new_hints, field)
                if value:
                    setattr(hints, field, value)
            hints.excluded_case_ids += [x for x in new_hints.excluded_case_ids if x not in hints.excluded_case_ids]
            hints.raw_mentions += new_hints.raw_mentions
        if has_hints(new_hints) or facts.intent:
            state.events.append("memory: intent/case hints saved")
        memory.notes += facts.notes
        for doc in facts.missing_documents:
            if doc not in state.missing_documents:
                state.missing_documents.append(doc)

    def _selected_claim(self, state: SessionState) -> Claim | None:
        """The selected claim, read directly (no tool gate: it is only for internal checks)."""
        if not (state.verified_party_id and state.selected_case_id):
            return None
        return self.tools.store.get_claim(state.selected_case_id, state.verified_party_id)

    # --- VERIFY_ID ---

    def step_verify_id(self, state: SessionState, facts: ExtractedFacts) -> TurnPlan | None:
        """VERIFY_ID: find out who the caller is. STRICT."""
        if facts.wants_to_end:
            self.goto(state, Phase.ENDED, "caller ended the call before verification")
            return TurnPlan(phase=state.phase, goal="The caller wants to end the call. Say goodbye politely.")
        if state.caller_role == CallerRole.REPRESENTATIVE:
            return self.step_verify_representative(state, facts)

        result = self.tools.call("verify_identity", state)
        state.events.append(f"verification: {result.status.value} (matched {result.matched_count})")
        if result.status == VerificationStatus.VERIFIED:
            state.verified_party_id = result.party_id
            if state.caller_role == CallerRole.UNKNOWN:
                state.caller_role = CallerRole.POLICYHOLDER
            self.goto(state, Phase.RESOLVE_INTENT, "identity verified")
            return None
        return self._identity_plan(state, result, about="the caller")

    def _identity_plan(self, state: SessionState, result, about: str) -> TurnPlan:
        """The plan when the identity is not verified yet (NEED_MORE or MISMATCH)."""
        given = [FIELD_LABELS[f] for f in COUNTED_FIELDS if getattr(state.identity, f)]
        remaining = [
            FIELD_LABELS[f] for f in COUNTED_FIELDS if not getattr(state.identity, f) and f not in state.fields_refused
        ]
        facts = {
            "identity_of": about,
            "details_already_given": given,
            "details_you_can_ask_for": remaining,
            "details_refused": [FIELD_LABELS[f] for f in state.fields_refused],
            "number_of_details_still_needed": result.still_needed,
            "caller_request_to_handle_after_verification": self._memory_summary(state),
        }
        if result.status == VerificationStatus.MISMATCH:
            goal = (
                f"Say politely that the details for {about} do not match our records. "
                "Ask the caller to check the details and give them again. Do not say which detail is wrong."
            )
        elif not given:
            goal = (
                f"Explain that you must verify the identity of {about} first. Ask for at least 3 of the details "
                "in details_you_can_ask_for. If the caller already said why they call, say that you will help "
                "with it right after verification."
            )
        else:
            goal = (
                f"Thank the caller. Ask for {result.still_needed} more detail(s) from details_you_can_ask_for. "
                "Do not say whether any detail matched."
            )
        return TurnPlan(phase=state.phase, goal=goal, facts=facts)

    def _memory_summary(self, state: SessionState) -> str | None:
        """The caller's own words about the reason for the call. Safe to repeat before verification."""
        mentions = state.memory.case_hints.raw_mentions
        if mentions:
            return "; ".join(mentions)
        if state.memory.intent:
            return state.memory.intent.value.replace("_", " ")
        return None

    def step_verify_representative(self, state: SessionState, facts: ExtractedFacts) -> TurnPlan | None:
        """
        VERIFY_ID for a caller who calls for another person (Q4 in product_map.md):
            1. Verify the CUSTOMER identity with 3 fields (the representative gives them).
            2. Check representatives.json: can this representative call for this customer?
               The representative does not verify their own identity.
            3. Request consent from the customer. Check the status on each turn.
            4. Approved -> verified. Still pending after max polls -> say that we cannot continue
               without consent, and offer to send the request again.
        """
        if not state.pending_party_id:
            result = self.tools.call("verify_identity", state)
            state.events.append(f"verification (policyholder via representative): {result.status.value}")
            if result.status != VerificationStatus.VERIFIED:
                return self._identity_plan(state, result, about="the policyholder")
            state.pending_party_id = result.party_id

        if not state.rep_name:
            return TurnPlan(
                phase=state.phase,
                goal="Thank the caller. Ask for the caller's own full name, because they call for somebody else.",
            )

        rep = self.tools.call("find_representative", state, buyer_party_id=state.pending_party_id)
        if rep is None:
            state.events.append(f"representative '{state.rep_name}' is not authorized")
            return TurnPlan(
                phase=state.phase,
                goal=(
                    "Explain politely that you can discuss the account only with the policyholder or with an "
                    "authorized representative on file, and the caller is not on file. Share no account details. "
                    "Offer: the policyholder can join the conversation and verify."
                ),
            )

        if state.consent_status is None or (state.consent_status == "timed_out" and facts.confirms):
            self.tools.call("request_consent", state, buyer_party_id=state.pending_party_id)
        elif state.consent_status == "timed_out":
            return self._consent_timeout_plan(state)

        status = self.tools.call("check_consent", state)
        if status == "approved":
            state.verified_party_id = state.pending_party_id
            self.goto(state, Phase.RESOLVE_INTENT, "representative authorized and consent approved")
            return None
        if state.consent_polls >= self.settings.max_consent_polls:
            state.consent_status = "timed_out"
            return self._consent_timeout_plan(state)
        return TurnPlan(
            phase=state.phase,
            goal=(
                "Tell the caller that the details are confirmed, and that we sent an approval request to the "
                "policyholder's phone on file. We can continue after the policyholder approves it. "
                "Ask the caller to tell you when the policyholder has approved it."
            ),
            facts={"representative": state.rep_name, "relationship": state.rep_relationship},
        )

    def _consent_timeout_plan(self, state: SessionState) -> TurnPlan:
        return TurnPlan(
            phase=state.phase,
            goal=(
                "Say that we did not receive the policyholder's approval, so we cannot discuss the account. "
                "Ask if the caller wants us to send the approval request again."
            ),
        )

    # --- RESOLVE_INTENT ---

    def step_resolve_intent(self, state: SessionState, facts: ExtractedFacts) -> TurnPlan | None:
        """RESOLVE_INTENT: find out which claim the caller means. MEDIUM freedom."""
        claims = self.tools.call("list_claims", state)
        if not claims:
            state.topics_discussed.append("Checked the account: there are no claims on file.")
            self.goto(state, Phase.POST_PROCESS, "no claims on file")
            return None
        if facts.wants_to_end:
            self.goto(state, Phase.POST_PROCESS, "caller has no more questions")
            return None

        hints = state.memory.case_hints
        if state.pending_case_id:
            if facts.confirms is True:
                return self._select_case(state, state.pending_case_id, "caller confirmed the claim")
            if facts.confirms is False:
                hints.excluded_case_ids.append(state.pending_case_id)
                state.events.append(f"caller rejected {state.pending_case_id}")
                state.pending_case_id = None
            elif not has_hints(facts.case_hints):
                return self._confirm_plan(state, claims, state.pending_case_id)

        resolution = resolve_case(claims, hints, self.llm)
        state.events.append(f"case resolution: {resolution.status.value} {resolution.candidate_ids} ({resolution.reason})")

        if resolution.status == ResolutionStatus.ONE_MATCH:
            if resolution.by_llm:
                state.pending_case_id = resolution.case_id
                return self._confirm_plan(state, claims, resolution.case_id)
            return self._select_case(state, resolution.case_id, "one claim matches the caller's hints")

        all_briefs = [claim_brief(c) for c in claims]
        if resolution.status == ResolutionStatus.MANY_MATCHES:
            state.candidate_case_ids = resolution.candidate_ids
            by_id = {c.case_id: c for c in claims}
            return TurnPlan(
                phase=state.phase,
                goal="Several claims fit what the caller said. List them briefly and ask which one they mean.",
                facts={"matching_claims": [claim_brief(by_id[i]) for i in resolution.candidate_ids],
                       "caller_said": self._memory_summary(state)},
            )
        if resolution.status == ResolutionStatus.NO_MATCH:
            said = self._memory_summary(state)
            state.memory.case_hints = CaseHints()  # Start fresh, so old hints do not block the next answer.
            return TurnPlan(
                phase=state.phase,
                goal="Say that no claim on the account matches what the caller described. "
                "List the claims briefly and ask which one they mean.",
                facts={"caller_said": said, "claims_on_account": all_briefs},
            )
        # NO_HINTS
        return TurnPlan(
            phase=state.phase,
            goal="Thank the caller for verifying. Ask which claim they are calling about. List the claims briefly.",
            facts={"claims_on_account": all_briefs},
        )

    def _confirm_plan(self, state: SessionState, claims: list[Claim], case_id: str) -> TurnPlan:
        claim = next(c for c in claims if c.case_id == case_id)
        return TurnPlan(
            phase=state.phase,
            goal="Ask the caller to confirm that this is the claim they mean.",
            facts={"claim_to_confirm": claim_brief(claim), "caller_said": self._memory_summary(state)},
        )

    def _select_case(self, state: SessionState, case_id: str, reason: str) -> None:
        state.selected_case_id = case_id
        state.pending_case_id = None
        state.candidate_case_ids = []
        state.case_intro_done = False
        state.events.append(f"claim selected: {case_id} ({reason})")
        self.goto(state, Phase.PROCESS_CASE, "claim identified")
        return None

    # --- PROCESS_CASE ---

    def step_process_case(self, state: SessionState, facts: ExtractedFacts) -> TurnPlan | None:
        """PROCESS_CASE: answer the caller about the claim. FLEXIBLE, but grounded."""
        claim = self.tools.call("get_claim", state)
        if claim is None or hints_conflict_with(state.memory.case_hints, claim):
            state.selected_case_id = None
            self.goto(state, Phase.RESOLVE_INTENT, "caller asks about a different claim")
            return None
        if facts.wants_to_end and not facts.question:
            self.goto(state, Phase.POST_PROCESS, "caller has no more questions")
            return None

        today = self.settings.demo_today
        plan_facts: dict = {
            "today": today,
            "claim": claim.model_dump(exclude={"party_id"}),
            "money_field_meanings": {
                name: info["description"] for name, info in self.tools.store.claim_schema["field_descriptions"].items()
            },
            "caller_question": facts.question,
            "caller_intent": state.memory.intent.value if state.memory.intent else None,
        }
        if claim.appeal_deadline:
            plan_facts["appeal_deadline_passed"] = claim.appeal_deadline < today
        if claim.documents_needed:
            plan_facts["document_requirements"] = [s.text for s in self.tools.call("get_document_requirements", state)]
        if state.missing_documents:
            plan_facts["options_for_missing_documents"] = [
                s.text
                for doc in state.missing_documents
                for s in self.tools.call("get_document_alternatives", state, document_name=doc)
            ]
        if facts.question or claim.documents_needed:
            snippets = self.tools.call("get_followup_snippets", state, question=facts.question, intent=state.memory.intent)
            plan_facts["approved_followup_answers"] = [s.model_dump(exclude={"source"}) for s in snippets]

        # Record the conversation for the email summary.
        if not state.case_intro_done:
            state.topics_discussed.append(f"The status of claim {claim.case_id} ({claim.case_type}): {claim.status}.")
        if facts.question:
            state.topics_discussed.append(f"Question: {facts.question}")
        for doc in facts.missing_documents:
            state.topics_discussed.append(f"Options if the {doc} is not available.")

        if not state.case_intro_done:
            state.case_intro_done = True
            goal = (
                "Tell the caller which claim you found (ID, type, date filed) and explain its status in plain words. "
                "If it was denied, explain the reason and which documents are needed. "
                "If the caller already asked a question, answer it too. Then ask what else they want to know."
            )
        elif facts.question or facts.missing_documents:
            goal = "Answer the caller's question with the facts. Keep it short. Then ask if they need anything else."
        else:
            goal = "Respond briefly to the caller. Ask if they have another question about this claim."
        return TurnPlan(phase=state.phase, goal=goal, facts=plan_facts)

    # --- POST_PROCESS ---

    def next_steps_for(self, state: SessionState, claim: Claim | None) -> list[str]:
        """The follow-up items for the email summary. Built from the claim and the guidance. Deterministic."""
        if claim is None:
            return []
        if claim.status == "denied" and claim.documents_needed:
            guidance = self.tools.guidance
            docs = ", ".join(claim.documents_needed)
            steps = [
                f"Submit the missing documents for claim {claim.case_id} ({docs}) through the member portal "
                "or the claim upload link. Support can help arrange fax or mail if upload is not available.",
                "Please submit the documents within a week.",
            ]
            if claim.appeal_deadline:
                steps.append(f"The appeal deadline is {claim.appeal_deadline}.")
            processing = guidance.data["claim_followup_settings"]["average_processing_time_after_submission"]["en"]
            steps.append(f"After the documents are received, the review restarts. Processing is {processing}.")
            for doc in state.missing_documents:
                alt = guidance.alternatives_for_document(doc)[0].text
                steps.append(alt.split(". ")[0].rstrip(".") + ".")
            return steps
        if claim.status == "open":
            return [f"Claim {claim.case_id} is in progress. No action is needed from you now."]
        return [f"Claim {claim.case_id} is {claim.status}. No action is needed."]

    def step_post_process(self, state: SessionState, facts: ExtractedFacts) -> TurnPlan | None:
        """POST_PROCESS: offer the email summary. The caller chooses: send or skip."""
        if state.email_offered:
            choice = facts.email_choice if facts.email_choice is not None else facts.confirms
            if choice is True:
                summary = build_summary(state, self.tools.store)
                self.tools.call("send_email_summary", state, summary=summary)
                self.goto(state, Phase.ENDED, "email summary sent")
                return TurnPlan(
                    phase=state.phase,
                    goal="Confirm that the summary email was sent to the email address on file. Thank the caller. Say goodbye.",
                    facts={"email_on_file": _mask_email(summary.to_address)},
                )
            if choice is False:
                self.goto(state, Phase.ENDED, "caller skipped the email")
                return TurnPlan(
                    phase=state.phase,
                    goal="Confirm that no email will be sent. Thank the caller. Say goodbye.",
                )
            if (facts.question or has_hints(facts.case_hints)) and state.selected_case_id:
                state.email_offered = False
                self.goto(state, Phase.PROCESS_CASE, "caller has another question")
                return None

        claim = self.tools.call("get_claim", state)
        state.next_steps = self.next_steps_for(state, claim)
        summary = build_summary(state, self.tools.store)
        first_time = not state.email_offered
        state.email_offered = True
        if first_time and facts.email_choice is not None:
            return self.step_post_process(state, facts)  # The caller already chose in this message.
        return TurnPlan(
            phase=state.phase,
            goal=(
                "Give a very short recap of the claim status and the next steps. Then offer to send this summary "
                "by email to the email address on file. Make clear the caller can say yes or no."
                if first_time
                else "Ask again, briefly: does the caller want the summary email, yes or no?"
            ),
            facts={
                "claim_status": claim_status_line(claim) if claim else "No claim was discussed.",
                "discussed": state.topics_discussed,
                "next_steps": summary.next_steps,
                "email_on_file": _mask_email(summary.to_address),
            },
        )

    # --- ENDED ---

    def step_ended(self, state: SessionState, facts: ExtractedFacts) -> TurnPlan:
        return TurnPlan(
            phase=state.phase,
            goal="The conversation is complete. Say goodbye politely. For more help, the caller can start a new conversation.",
        )


def _mask_email(address: str) -> str:
    """"margaret@email.com" -> "m*******@email.com". Enough for the caller to recognize it."""
    name, _, domain = address.partition("@")
    return f"{name[:1]}{'*' * max(len(name) - 1, 1)}@{domain}"
