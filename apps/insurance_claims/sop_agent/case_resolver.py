"""
Find which claim the caller means, from the case hints in memory.

WHERE THIS FITS:
    The controller calls this module in RESOLVE_INTENT, and when the caller
    switches to a different claim.
    Input:  state.memory.case_hints + the claims of the verified customer.
    Output: A claim, a list of candidates, or "no match".

To handle messages of varying ambiguity, we use up to two stages
- Stage 1 (code): filter with the STRUCTURED hints. This is exact and safe.
- Stage 2 (LLM): if more than one candidate remains AND there are hints in memory,
    ask the LLM to rank the candidates. This is the "medium freedom" of RESOLVE_INTENT.
The LLM only chooses from the claims that the code gives. The code checks that the
returned case_id is in that list. If not, the code ignores the LLM answer.
"""

from enum import Enum

from pydantic import BaseModel

from .llm_client import LLMClient, LLMError
from .models import Claim
from .state import CaseHints


class ResolutionStatus(str, Enum):
    ONE_MATCH = "one_match"
    MANY_MATCHES = "many_matches"
    NO_MATCH = "no_match"
    NO_HINTS = "no_hints"


class Resolution(BaseModel):
    status: ResolutionStatus
    case_id: str | None = None
    candidate_ids: list[str] = []
    reason: str = ""
    by_llm: bool = False


class _Ranking(BaseModel):
    ranked_ids: list[str]
    confident: bool


def has_structured_hints(hints: CaseHints) -> bool:
    return any([hints.case_id, hints.case_type, hints.status, hints.date_from, hints.date_to])


def has_free_text_hints(hints: CaseHints) -> bool:
    return bool(hints.description or hints.raw_mentions)


def filter_claims(claims: list[Claim], hints: CaseHints) -> list[Claim]:
    """
    Stage 1. Keep the claims that match ALL the structured hints
    """
    result = [c for c in claims if c.case_id not in {x.upper() for x in hints.excluded_case_ids}]
    if hints.case_id:
        return [c for c in result if c.case_id == hints.case_id.strip().upper()]
    if hints.case_type:
        result = [c for c in result if c.case_type.lower() == hints.case_type.lower()]
    if hints.status:
        result = [c for c in result if c.status.lower() == hints.status.lower()]
    # ISO dates ("YYYY-MM-DD") compare correctly as strings.
    if hints.date_from:
        result = [c for c in result if c.created_at >= hints.date_from]
    if hints.date_to:
        result = [c for c in result if c.created_at <= hints.date_to]
    return result


RANK_PROMPT = """\
You match a caller's description to one of their insurance claims.
Order the candidate claims from the best fit to the worst fit.
Set confident to true only if the first claim clearly fits and the others clearly do not.
Use only IDs from the candidate list.
"""


def rank_with_llm(candidates: list[Claim], hints: CaseHints, llm: LLMClient) -> tuple[list[str], bool]:
    """
    Stage 2. Ask the LLM to order the candidates by how well they fit current hints.
    Return (ranked IDs, confident). The IDs are always a subset of the candidates.
    """
    listing = "\n".join(
        f"- {c.case_id}: {c.case_type}, {c.status}, filed {c.created_at}. {c.summary}" for c in candidates
    )
    clues = "; ".join(filter(None, [hints.description, *hints.raw_mentions]))
    messages = [{"role": "user", "content": f"Candidates:\n{listing}\n\nCaller description: {clues}"}]
    try:
        ranking = llm.extract(RANK_PROMPT, messages, _Ranking)
    except LLMError:
        return [c.case_id for c in candidates], False
    valid = [c.case_id for c in candidates]
    from_llm = [cid for cid in dict.fromkeys(ranking.ranked_ids) if cid in valid] 
    ranked = from_llm + [cid for cid in valid if cid not in from_llm]
    return ranked, ranking.confident and bool(from_llm)


def resolve_case(claims: list[Claim], hints: CaseHints, llm: LLMClient) -> Resolution:
    """Run stage 1, then stage 2 if necessary. Return a Resolution."""
    if not (has_structured_hints(hints) or has_free_text_hints(hints)):
        return Resolution(status=ResolutionStatus.NO_HINTS, candidate_ids=[c.case_id for c in claims])

    candidates = filter_claims(claims, hints)
    if not candidates:
        return Resolution(status=ResolutionStatus.NO_MATCH, reason="no claim matches the structured hints")
    if len(candidates) == 1:
        return Resolution(
            status=ResolutionStatus.ONE_MATCH,
            case_id=candidates[0].case_id,
            candidate_ids=[candidates[0].case_id],
            reason="one claim matches the structured hints" if has_structured_hints(hints) else "only one claim",
        )
    if has_free_text_hints(hints):
        ranked, confident = rank_with_llm(candidates, hints, llm)
        if confident:
            return Resolution(
                status=ResolutionStatus.ONE_MATCH,
                case_id=ranked[0],
                candidate_ids=ranked,
                reason="LLM ranked the free-text hints (confident)",
                by_llm=True,
            )
        return Resolution(status=ResolutionStatus.MANY_MATCHES, candidate_ids=ranked, reason="LLM not confident")
    return Resolution(
        status=ResolutionStatus.MANY_MATCHES,
        candidate_ids=[c.case_id for c in candidates],
        reason="more than one claim matches the structured hints",
    )
