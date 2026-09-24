# Agent design

How to view and run the demo: see the [top-level README](../../README.md).

## One turn

```
caller message
  → 1. EXTRACT  (LLM)   extraction.py     message → structured facts, for all phases
  → 2. DECIDE   (code)  controller.py     apply the SOP → TurnPlan (goal + allowed facts)
  → 3. PHRASE   (LLM)   responder.py      TurnPlan → natural reply
  → 4. CHECK    (code)  output_guard.py   block leaks → final reply
```

`agent.py` runs these steps. The `SessionState` (`state.py`) is the source of truth. It holds the phase,
what the caller said, what was verified, the memory, and a log of decisions for the debug panel.

## Phases

| Phase | Freedom | What the code does | What the LLM does |
|---|---|---|---|
| VERIFY_ID | strict | Matches ≥3 identity fields against the records (`verification.py`). Representatives also need an authorization record and consent. | Extracts fields from free text; asks for missing details. |
| RESOLVE_INTENT | medium | Filters the claims by structured hints (`case_resolver.py`); accepts only claim IDs from its own list. | Ranks the candidates for vague hints; the caller confirms. |
| PROCESS_CASE | flexible | Releases the claim record, the field meanings, and the approved guideline text (`guidance.py`). | Explains and answers, only from those facts. |
| POST_PROCESS | structured | Builds the email summary from what it recorded (`summary.py`); sends only after "yes", only to the address on file. | Recaps and asks. |

One message can pass through several phases. For example, the task's test case goes from VERIFY_ID through
RESOLVE_INTENT to PROCESS_CASE in one turn.

## Key mechanisms

- **Memory.** Extraction runs in every phase and saves intent and claim hints into `state.memory`,
  but only the controller changes the phase. Hints accumulate over turns. A hint that points to a different
  claim replaces the old hints.
- **TurnPlan.** The only channel from the controller to the reply LLM: a goal, constraints, and the facts
  it may use. Before verification, the plan contains no account data.
- **Phase-gated tools** (`tools.py`). `TOOLS_BY_PHASE` lists the allowed tools, and any other call raises
  `ToolNotAllowed`. The data store (`data_store.py`) returns a claim only to its owner.
- **Scope guard** (`scope_guard.py`). Off-topic messages are declined. After 3 in a row, the reply suggests
  a human representative. An in-scope message resets the counter.
- **Model adapters** (`llm_client.py`). `LLMClient` has two methods: `extract()` (structured output) and
  `generate()` (text). There is one adapter for Anthropic and one for OpenAI-compatible APIs, and the providers
  are presets in `config.py`. The extraction schema is flat (every field required, no nulls), because
  providers reject complex structured-output schemas. If a model has no structured output, the adapter asks
  for JSON in the prompt and validates it with Pydantic.
- **Fail safe.** If extraction fails, the agent changes nothing and asks the caller to repeat.

## Tests

- `tests/test_verification.py`: identity matching (formats, aliases, national ID, refusals, mismatches).
- `tests/test_controller.py`: SOP rules without an LLM (phase gate, one-turn demo case, consent timeout,
  off-topic counter, skipped email).
- `tests/scenarios.py` + `tests/test_conversations.py`: 12 scripted conversations with the real model
  (`pytest -m llm -s`).
