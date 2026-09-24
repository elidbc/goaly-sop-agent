"""
Approved document guidance from fixtures/required_document_guideline.json. Plain code, no LLM.

PROCESS_CASE gives this text to the responder, which rephrases it. The LLM must not invent rules.
For follow-up questions, the responder gets ALL the items that apply, with keyword_match and
intent_match flags, and chooses. Keyword matching alone is too fragile.

Data quirk: claims say "pathology report", the guideline says "original pathology report".
resolve_document_key() maps them. "diagnosis report" has no entry, so the default text applies.
"""

import re

from pydantic import BaseModel

from .models import Claim
from .state import Intent


class GuidanceSnippet(BaseModel):
    """One piece of approved text. `source` is for debugging."""

    source: str
    text: str                     # Placeholders already filled in.
    keyword_match: bool = False
    intent_match: bool = False


def documents_phrase(documents: list[str]) -> str:
    """["pathology report", "office note"] -> "the pathology report and the office note"."""
    items = [f"the {d}" for d in documents]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


class DocumentGuidance:
    def __init__(self, guideline_data: dict, language: str = "en") -> None:
        self.data = guideline_data
        self.lang = language

    def _text(self, entry: dict) -> str:
        return entry.get(self.lang) or next(iter(entry.values()))

    def resolve_document_key(self, document_name: str) -> str | None:
        """"pathology report" -> "original pathology report" (one contains the other), or None."""
        name = document_name.strip().lower()
        for key in self.data.get("document_guidance", {}):
            if name in key or key in name:
                return key
        return None

    def requirements_for_claim(self, claim: Claim) -> list[GuidanceSnippet]:
        """General + case-type + per-document rules for the documents this claim needs."""
        snippets = [GuidanceSnippet(source="default_guidance", text=self._text(self.data["default_guidance"]))]
        case_rule = self.data.get("case_type_guidance", {}).get(claim.case_type)
        if case_rule:
            snippets.append(GuidanceSnippet(source=f"case_type_guidance/{claim.case_type}", text=self._text(case_rule)))
        for doc in claim.documents_needed:
            key = self.resolve_document_key(doc)
            if key:
                text = self._text(self.data["document_guidance"][key])
                snippets.append(GuidanceSnippet(source=f"document_guidance/{key}", text=f"{doc}: {text}"))
            else:
                snippets.append(
                    GuidanceSnippet(
                        source=f"document_guidance/(none for {doc})",
                        text=f"{doc}: there is no document-specific rule. Follow the general upload rules.",
                    )
                )
        return snippets

    def alternatives_for_document(self, document_name: str) -> list[GuidanceSnippet]:
        """What the caller can do without this document."""
        alternatives = self.data.get("document_alternative_guidance", {})
        key = self.resolve_document_key(document_name)
        if key and key in alternatives:
            source, text = f"document_alternative_guidance/{key}", self._text(alternatives[key])
        else:
            source, text = "document_alternative_guidance/default", self._text(alternatives["default"])
        snippets = [GuidanceSnippet(source=source, text=f"{document_name}: {text}")]
        exhausted = self.data.get("claim_followup_settings", {}).get("human_review_after_document_alternatives_exhausted")
        if exhausted:
            snippets.append(
                GuidanceSnippet(
                    source="claim_followup_settings/human_review_after_document_alternatives_exhausted",
                    text=self._text(exhausted),
                )
            )
        return snippets

    def followup_snippets(self, question: str | None, intent: Intent | None, claim: Claim) -> list[GuidanceSnippet]:
        """Every follow-up item that applies to this claim, with match flags, plus the fallback."""
        q = (question or "").lower()
        snippets = []
        for item in self.data.get("claim_followup_guidance", []):
            if item.get("requires_documents") and not claim.documents_needed:
                continue
            snippets.append(
                GuidanceSnippet(
                    source=f"claim_followup_guidance/{item['topic']}",
                    text=self.fill_template(self._text(item), claim),
                    keyword_match=any(phrase in q for phrase in item.get("match_any", [])),
                    intent_match=intent is not None and intent.value in item.get("intent_hints", []),
                )
            )
        snippets.append(
            GuidanceSnippet(source="claim_followup_fallback", text=self._text(self.data["claim_followup_fallback"]))
        )
        return snippets

    def fill_template(self, text: str, claim: Claim) -> str:
        """Fill {case_id}, {documents}, and the claim_followup_settings values. Unknown placeholders stay."""
        values = {"case_id": claim.case_id, "documents": documents_phrase(claim.documents_needed)}
        for name, entry in self.data.get("claim_followup_settings", {}).items():
            values[name] = self._text(entry)
        return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), text)
