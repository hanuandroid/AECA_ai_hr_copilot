"""Deterministic response guard for Policy RAG answers (no second LLM involved).

Checks, in order: grounded flag, citation presence/validity, empty answer, sensitive values,
sensitive terms not present in the cited evidence, claims of having performed actions or
changed roles, echoed injection phrases, and numbers not supported by the cited evidence.
"""

import re
from dataclasses import dataclass

from app.services.ai.rag.models import PolicyLLMOutput, RetrievedChunk
from app.services.ai.redaction import contains_sensitive_value

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_LIST_MARKER_RE = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
_SENSITIVE_TERM_RE = re.compile(
    r"(?i)\b(salary|salaries|compensation|bank|account number|ifsc|pan|date of birth|dob|password|pf|uan|esi)\b"
)
# Claims that the assistant itself did something, or that the user's specific item changed state.
# (Generic policy statements such as "days are deducted once a request has been approved" pass.)
_ACTION_VERBS = r"(?:approved|rejected|submitted|created|assigned|updated|deactivated|granted|applied|processed|revealed)"
_ACTION_CLAIM_RE = re.compile(
    rf"(?i)\b(?:i|we)(?:'ve|\s+have|\s+has)?\s+(?:just\s+|now\s+)?{_ACTION_VERBS}\b"
    rf"|\byour\s+(?:leave|request|ticket|application|announcement)\b[^.\n]{{0,40}}"
    rf"\b(?:has\s+been|have\s+been|was|is\s+now)\s+{_ACTION_VERBS}\b"
)
_ROLE_CLAIM_RE = re.compile(
    r"(?i)\b(you\s+are\s+now|you\s+now\s+have|acting\s+as)\s+(an?\s+)?(admin|administrator|manager|hr\s+admin)\b"
    r"|\b(admin|administrator)\s+(access|privileges|rights)\s+(granted|enabled)\b"
)
_INJECTION_ECHO_RE = re.compile(
    r"(?i)(ignore\s+(all\s+)?(previous|prior|above)\s+instructions|system\s+prompt|developer\s+mode)"
)


def claims_action_or_role(text: str) -> bool:
    """Shared with the SQL result formatter guard."""
    return bool(_ACTION_CLAIM_RE.search(text) or _ROLE_CLAIM_RE.search(text) or _INJECTION_ECHO_RE.search(text))


@dataclass(frozen=True, slots=True)
class GuardDecision:
    ok: bool
    reason: str | None
    cited: tuple[RetrievedChunk, ...] = ()


def check_answer(output: PolicyLLMOutput, labelled: dict[str, RetrievedChunk], question: str) -> GuardDecision:
    if not output.grounded:
        return GuardDecision(False, "not_grounded")
    labels = [label.strip() for label in output.citations if label and label.strip()]
    if not labels:
        return GuardDecision(False, "no_citations")
    if any(label not in labelled for label in labels):
        return GuardDecision(False, "invalid_citation")
    answer = output.answer.strip()
    if not answer:
        return GuardDecision(False, "empty_answer")

    cited = tuple(labelled[label] for label in dict.fromkeys(labels))
    evidence = "\n".join(f"{item.chunk.policy_title} {item.chunk.section} {item.chunk.text}" for item in cited)

    if contains_sensitive_value(answer):
        return GuardDecision(False, "sensitive_value")
    evidence_terms = {match.lower() for match in _SENSITIVE_TERM_RE.findall(evidence)}
    if any(term.lower() not in evidence_terms for term in _SENSITIVE_TERM_RE.findall(answer)):
        return GuardDecision(False, "sensitive_term_unsupported")
    if _ACTION_CLAIM_RE.search(answer) or _ROLE_CLAIM_RE.search(answer):
        return GuardDecision(False, "action_or_role_claim")
    if _INJECTION_ECHO_RE.search(answer):
        return GuardDecision(False, "injection_echo")

    supported_numbers = set(_NUMBER_RE.findall(evidence)) | set(_NUMBER_RE.findall(question))
    answer_numbers = set(_NUMBER_RE.findall(_LIST_MARKER_RE.sub("", answer)))
    if not answer_numbers <= supported_numbers:
        return GuardDecision(False, "unsupported_number")

    return GuardDecision(True, None, cited)
