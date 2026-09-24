# Insurance Claims SOP Agent

A chat agent for insurance claims support. It follows a fixed Standard Operating Procedure (SOP):

**VERIFY_ID → RESOLVE_INTENT → PROCESS_CASE → POST_PROCESS**

The code controls the phase order, the safety gates, and the allowed actions.
The LLM understands the caller and writes natural replies.

## Setup

**Hosted demo:** https://goaly-sop-agent-gsuy.onrender.com (no setup: open it and chat).
It runs `openai/gpt-6-luna` through OpenRouter. The free server sleeps when nobody uses it,
so the first visit can take about a minute to load. A reply takes about 10 seconds.

To run it locally, you need Python 3.10 or later and an API key from one of the supported providers:
**OpenRouter** (one key for OpenAI, Google, Anthropic, Meta, and other models), **OpenAI**, or **Anthropic**.

```bash
cd apps/insurance_claims
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Put your provider and key in `.env`, for example:

```
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
```

You can also skip `.env`. Then choose the provider and paste the key under **Model settings** on the page.

## Run the test UI

```bash
.venv/bin/python app.py
```

Open http://127.0.0.1:7860. Type as a caller. The **debug panel** on the right shows the current phase,
the verification status, the memory, and the decisions of each turn.

## Demo script

1. Send the README test case (it is also an example button on the page):
   > I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied healthcare
   > claim from January. DOB is 1985-03-15, SSN last four is 4472.

   The agent verifies Margaret (3 fields), uses the remembered hint to find claim CL-2048, and explains it.
2. Ask follow-up questions: "How do I submit the documents?", "I don't have the pathology report. What can I do?"
3. Say "That's all, thanks." The agent recaps and offers an email summary. Answer yes or no.
   The debug panel shows the sent (mock) email.

Other things to try:
- Give a claim hint before you give identity details. The agent stays in VERIFY_ID but remembers the hint.
- Ask about a claim before verification. The agent gives no claim details.
- Refuse one detail ("I'd rather not give my SSN"). The agent asks for another detail.
- Ask off-topic questions ("What is RL?"). The agent declines. After 3 in a row, it suggests a human representative.
- Call as a representative: "I'm David Chen, calling for my mother Margaret Chen." The agent verifies
  Margaret's details, checks that David is on file, and requests Margaret's consent (mock).

## Settings (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `openrouter`, `openai`, or `anthropic` (presets in `sop_agent/config.py`). |
| `LLM_MODEL` | per provider | Default: `openai/gpt-6-luna` (OpenRouter), `gpt-5-mini` (OpenAI), `claude-sonnet-5` (Anthropic). |
| `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | The key for the chosen provider. `LLM_API_KEY` works for any provider. |
| `LLM_BASE_URL` | — | Any other OpenAI-compatible server (for example a local Ollama). |
| `DEMO_TODAY` | `2026-02-15` | The date the agent uses as "today" (the fixture deadlines are in early 2026). |
| `CONSENT_SCENARIO` | `default` | The mock consent answer: `default` (approved) or `timeout` (never approved). |

## Tests

```bash
.venv/bin/pytest                 # fast tests, no API calls (identity verification)
.venv/bin/pytest -m llm -s       # full conversations with the real model (costs a few cents), prints transcripts
```

## Model independence

The harness talks to models through one small interface (`sop_agent/llm_client.py`) with two adapters:
the Anthropic API and the OpenAI-compatible API (OpenAI, OpenRouter, and others).
All 12 conversation scenarios pass with **openai/gpt-6-luna** (via OpenRouter) and **claude-sonnet-5** (Anthropic).

The SOP guarantees do not depend on the model. Identity verification, the phase gates, the data given
to each phase, and the output guard are plain code. A weaker model can write weaker replies, but it
cannot skip verification or leak claim data. (Example: google/gemini-3.1-flash-lite passed 11/12;
it did not recognize one off-topic question, and the harness still behaved correctly for what it got.)

## How it works

Each caller message goes through 4 steps (`sop_agent/agent.py`):

1. **Extract (LLM)**, `extraction.py`. The message becomes structured facts: identity fields, claim hints, intent, and yes/no answers.
   It extracts facts for all phases, so early information goes into memory.
2. **Decide (code)**, `controller.py`. The SOP state machine. It verifies identity with plain code (`verification.py`),
   calls phase-gated tools (`tools.py`), finds the claim (`case_resolver.py`), and makes a **TurnPlan**:
   the goal of the reply and the only facts that the reply may use.
3. **Phrase (LLM)**, `responder.py`. It writes a natural reply from the TurnPlan only.
   Before verification, the TurnPlan contains no claim data, so the LLM cannot leak it.
4. **Check (code)**, `output_guard.py`. A last regex check blocks claim IDs or amounts before verification.
