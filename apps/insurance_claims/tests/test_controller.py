"""
Offline tests of the SOP rules: hand-made ExtractedFacts in, state checked. No LLM.
"""

import dataclasses

import pytest

from sop_agent.config import Settings
from sop_agent.controller import SopController
from sop_agent.data_store import DataStore
from sop_agent.extraction import ExtractedFacts
from sop_agent.guidance import DocumentGuidance
from sop_agent.state import CallerRole, CaseHints, IdentityClaims, Phase, SessionState
from sop_agent.tools import ToolBox, ToolNotAllowed


class NoLLM:
    """A fake LLM. These tests must not need the model."""

    def extract(self, *args, **kwargs):
        raise AssertionError("The LLM must not be called in this test")

    generate = extract


def make_controller(**settings_changes) -> SopController:
    settings = dataclasses.replace(Settings(), **settings_changes)
    store = DataStore(settings.fixtures_dir)
    return SopController(ToolBox(store, DocumentGuidance(store.document_guidelines), settings), NoLLM())


MARGARET = IdentityClaims(full_name="Margaret Chen", dob="1985-03-15", id_last4="4472")


def test_phase_gate_blocks_claims_before_verification():
    controller = make_controller()
    state = SessionState()
    with pytest.raises(ToolNotAllowed):
        controller.tools.call("list_claims", state)


def test_demo_case_goes_to_process_case_in_one_turn():
    controller = make_controller()
    state = SessionState()
    facts = ExtractedFacts(
        identity=MARGARET,
        case_hints=CaseHints(case_type="healthcare", status="denied", date_from="2026-01-01", date_to="2026-01-31"),
    )
    plan = controller.advance(state, facts)
    assert state.phase == Phase.PROCESS_CASE
    assert state.selected_case_id == "CL-2048"
    assert plan.facts["claim"]["case_id"] == "CL-2048"


def test_hint_is_kept_while_verification_is_incomplete():
    controller = make_controller()
    state = SessionState()
    plan = controller.advance(state, ExtractedFacts(case_hints=CaseHints(status="denied", raw_mentions=["denied"])))
    assert state.phase == Phase.VERIFY_ID
    assert state.memory.case_hints.status == "denied"
    assert "claim" not in plan.facts   # No claim data before verification.


def test_representative_consent_timeout():
    controller = make_controller(consent_scenario="timeout")
    state = SessionState()
    rep = ExtractedFacts(identity=MARGARET, caller_role=CallerRole.REPRESENTATIVE, rep_name="David Chen")
    controller.advance(state, rep)
    assert state.consent_status == "pending"
    for _ in range(4):
        controller.advance(state, ExtractedFacts())
    assert state.consent_status == "timed_out"
    assert not state.is_verified()
    assert state.phase == Phase.VERIFY_ID
    # The caller asks to send the request again.
    controller.advance(state, ExtractedFacts(confirms=True))
    assert state.consent_status == "pending"


def test_unknown_representative_is_refused():
    controller = make_controller()
    state = SessionState()
    controller.advance(
        state, ExtractedFacts(identity=MARGARET, caller_role=CallerRole.REPRESENTATIVE, rep_name="Somebody Else")
    )
    assert not state.is_verified()
    assert state.consent_status is None   # No consent request for an unknown person.


def test_off_topic_counter_resets_and_suggests_human():
    controller = make_controller()
    state = SessionState()
    off = ExtractedFacts(in_scope=False)
    controller.advance(state, off)
    controller.advance(state, ExtractedFacts())       # in scope -> reset
    assert state.off_topic_strikes == 0
    plans = [controller.advance(state, off) for _ in range(3)]
    assert [p.suggest_human for p in plans] == [False, False, True]
    assert state.phase == Phase.VERIFY_ID            # No handoff. The phase does not change.


def test_email_skip_ends_without_sending():
    controller = make_controller()
    state = SessionState()
    controller.advance(state, ExtractedFacts(identity=MARGARET, case_hints=CaseHints(case_id="CL-2048")))
    controller.advance(state, ExtractedFacts(wants_to_end=True))
    assert state.phase == Phase.POST_PROCESS and state.email_offered
    controller.advance(state, ExtractedFacts(email_choice=False))
    assert state.phase == Phase.ENDED
    assert not state.email_sent
