"""
End-to-end scenarios with the real LLM (costs a few cents). Needs LLM_API_KEY.
Run: .venv/bin/pytest -m llm -s   (-s prints the transcripts)
"""

import pytest

from scenarios import SCENARIOS
from sop_agent.agent import SopAgent
from sop_agent.config import load_settings


def check_expectations(expect: dict, state, reply: str) -> list[str]:
    """Return a list of failed expectations (empty = all good)."""
    failures = []
    if "phase" in expect and state.phase.value != expect["phase"]:
        failures.append(f"phase is {state.phase.value}, expected {expect['phase']}")
    if "verified" in expect and state.is_verified() != expect["verified"]:
        failures.append(f"verified is {state.is_verified()}, expected {expect['verified']}")
    if "selected_case" in expect and state.selected_case_id != expect["selected_case"]:
        failures.append(f"selected_case is {state.selected_case_id}, expected {expect['selected_case']}")
    if "memory_intent" in expect:
        intent = state.memory.intent.value if state.memory.intent else None
        if intent != expect["memory_intent"]:
            failures.append(f"memory intent is {intent}, expected {expect['memory_intent']}")
    if "human_suggested" in expect and state.human_suggested != expect["human_suggested"]:
        failures.append(f"human_suggested is {state.human_suggested}, expected {expect['human_suggested']}")
    for text in expect.get("reply_excludes", []):
        if text.lower() in reply.lower():
            failures.append(f"reply contains forbidden text '{text}'")
    return failures


def run_scenario(agent: SopAgent, scenario: dict, verbose: bool = True) -> list[str]:
    """Run one scenario. Print the transcript. Return all the failures."""
    state = agent.new_session()
    failures = []
    if verbose:
        print(f"\n=== {scenario['name']} — {scenario['why']}")
        print(f"AGENT : {state.history[0].text}")
    for number, turn in enumerate(scenario["turns"], start=1):
        reply = agent.handle_turn(state, turn["caller"])
        problems = check_expectations(turn["expect"], state, reply.text)
        failures += [f"turn {number}: {p}" for p in problems]
        if verbose:
            print(f"CALLER: {turn['caller']}")
            print(f"AGENT : {reply.text}")
            print(f"        [phase={state.phase.value} verified={state.is_verified()} "
                  f"case={state.selected_case_id} events={reply.debug['this_turn']['events']}]")
            for p in problems:
                print(f"        FAIL: {p}")
    return failures


@pytest.fixture(scope="module")
def agent():
    settings = load_settings()
    if not settings.llm_api_key:
        pytest.skip(f"No API key for provider {settings.llm_provider}. Put it in .env to run the LLM tests.")
    return SopAgent(settings)


@pytest.mark.llm
@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
def test_scenario(agent, scenario):
    failures = run_scenario(agent, scenario)
    assert not failures, "\n".join(failures)
