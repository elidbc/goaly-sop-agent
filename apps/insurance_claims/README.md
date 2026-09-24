# Design choices

- **Deterministic code handles safety, LLM handles communication.** Each turn has four steps: an 
LLM extracts facts from the message, a the state machine runs the SOP, an LLM writes the reply, 
and a code check inspects it. The LLM itself cannot change the phase, verify a caller, 
or call a tool.
- **Safety by construction.** The reply LLM gets only the data that the current phase releases.
  Before verification, it has no access toclaim data, so it cannot leak any.
  Tools are gated by phase, and a regex output guard is a final line of defense to avoid exposing sensitive data.
- **Deterministic verification.** Identity is checked by plain code: at least 3 of the 5 README fields must match
  one record, with normalized formats and the aliases in the data. The policy number helps identify the claim, but does not count as personal ID.
  If only 2 fields match, the agent asks for one more field.
- **Memory across phases.** The extraction step saves facts for all phases in every turn. A claim hint given during
  verification is used after verification to potentially help find the claim.
- **Freedom by phase.** Structured claim hints (type, status, date) are filtered. Vague hints
  (e.g. "the one about the biopsy") are ranked by the LLM, and the caller confirms the result.
  In PROCESS_CASE the LLM answers freely, but only from the claim record and the approved guideline text.
- **Model agnostic.** One small interface with adapters: the Anthropic API and the OpenAI-compatible API
  (OpenAI, OpenRouter, and others). All 12 conversation scenarios pass with `openai/gpt-6-luna` and `claude-sonnet-5`.
  A weaker model can write weaker replies, but the state-managment code ensures it cannot break the SOP rules.
- **Scope kept to the task.** Off-topic questions are declined, and the agent suggests a human representative
  after 3 consecutive off-topic questions. There is no actual transfer to a human-in-the-loop. The email summary goes only to the address on file and is a mock. "Today" is fixed at 2026-02-15, so the fixture deadlines are still open and to 
  vary the status of claims in the DB.
