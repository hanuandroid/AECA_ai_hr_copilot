"""Intent classification for the copilot router.

Order of decisions (all deterministic except step 4):
  1. Refusal rules on the whole message (prompt injection, direct system access, destructive
     requests, sensitive data). A hit refuses the request before any LLM or service is called.
  2. Split the message into clauses ("... and ...", "...; ...", sentences).
  3. Per clause, deterministic signals: vague verb -> AMBIGUOUS, imperative action verb -> ACTION,
     policy wording -> POLICY, data-lookup wording -> SQL. Clauses without a signal merge into
     their neighbour.
  4. Only clauses that rules cannot classify go to the (fast) LLM, as structured output.
  5. A conservative plan: any AMBIGUOUS -> clarify; any UNSUPPORTED -> refuse; at most one ACTION;
     capability steps run in the fixed order POLICY -> SQL -> ACTION.

Phase 7 (conversation context, untrusted): "confirm"/"yes" messages never confirm anything; a
single-request follow-up ("does that apply to managers?", an answer to a clarification) keeps the
current message's own rule intent, else inherits the previous turn's route, else asks the LLM with
the delimited history. The service question becomes "<earlier user question>" + newline + "<message>".

Classification only chooses which specialised service receives the user's own text. It is never
authorization: every service re-authorizes the caller from the trusted AIUserContext.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

import structlog

from app.services.ai.audit.provider import observe
from app.services.ai.errors import AIClassificationError, AIInvalidOutputError, AIProviderError, AIUnavailableError
from app.services.ai.llm.base import LLMMessage, LLMProvider
from app.services.ai.orchestration.followup import (
    CONFIRM_IN_CHAT_MESSAGE,
    ConversationContext,
    contextual_question,
    is_confirmation_attempt,
    is_follow_up,
)
from app.services.ai.orchestration.models import (
    CAPABILITY_ORDER,
    Intent,
    PlanStep,
    RouterClassification,
    RoutingPlan,
)
from app.services.ai.orchestration.prompts import ROUTER_SYSTEM_PROMPT, build_router_message
from app.services.ai.rag.guard import claims_action_or_role
from app.services.ai.redaction import contains_sensitive_value, redact

logger = structlog.get_logger()

MIN_LLM_CONFIDENCE = 0.6
MAX_CLAUSES = 4  # more separate requests than this in one message -> clarification

_I = re.IGNORECASE


# --- 1. refusal rules (whole message) -------------------------------------------------------

_REFUSAL_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (category, re.compile(pattern, _I))
    for category, pattern in (
        # prompt injection / privilege escalation
        ("prompt_injection", r"\b(ignore|disregard|forget|override)\b[^.?!]{0,40}\b(instructions?|rules|prompts?|guardrails?|"
                             r"authori[sz]ation|permissions?|restrictions|security|checks?)\b"),
        ("prompt_injection", r"\byou are now\b|\bfrom now on,? you\b"),
        ("prompt_injection", r"\b(act|behave|respond|operate) as (an? |the )?(admin|administrator|superuser|root|system|"
                             r"developer|hr admin)\b"),
        ("prompt_injection", r"\bpretend (to be|you are|that you)\b"),
        ("prompt_injection", r"\b(i am|i'm|im) (an? |the )?(admin|administrator|superuser|root|hr admin|system)\b"),
        ("prompt_injection", r"\b(without|skip|skipping|bypass|bypassing|no need for|don't need|do not need)\b[^.?!]{0,20}"
                             r"\b(confirm\w*|authori[sz]\w*|approval|permission\w*|checks?|security|validation)\b"),
        ("prompt_injection", r"\b(disable|turn off|switch off)\b[^.?!]{0,30}\b(security|authori[sz]\w*|permissions?|guard\w*|"
                             r"confirm\w*|restrictions|validation)\b"),
        ("prompt_injection", r"\b(system|developer) (prompt|message|instructions?)\b|\b(jailbreak|dan mode|developer mode|god mode)\b"),
        ("prompt_injection", r"\b(grant|give)\b[^.?!]{0,15}\b(me|myself)\b[^.?!]{0,15}\b(admin|administrator|superuser|root|"
                             r"elevated|full)\s+(access|rights|role|privileges?|permissions?)\b"),
        ("prompt_injection", r"\bmake me (an? )?(admin|administrator|superuser|manager)\b"),
        ("prompt_injection", r"\b(change|set|update|elevate|promote)\b[^.?!]{0,15}\bmy (own )?(role|permissions?|access level)\b"),
        # direct system access (the copilot only exposes its fixed capabilities)
        ("direct_access", r"\b(database|db|tables?|api|endpoints?|backend)\s+directly\b|\bdirectly\b[^.?!]{0,30}"
                          r"\b(database|db|tables?|api|endpoints?|backend)\b"),
        ("direct_access", r"\b(run|execute|exec)\b[^.?!]{0,25}\b(sql|query|queries|command|script|code|shell|python|statement)\b"),
        ("direct_access", r"https?://|\bwww\.|\b(curl|wget|bash|powershell|cmd\.exe|subprocess|os\.system)\b|\b(eval|exec)\s*\("),
        ("direct_access", r"\b(get|post|put|patch|delete)\s+(request|method|call)\b|\b(http|rest)\s+(call|request|method)\b"),
        ("direct_access", r"\bselect\b[^.?!]{0,60}\bfrom\s+\w+|\binsert\s+into\b|\bdelete\s+from\b|\bupdate\s+\w+\s+set\b|"
                          r";\s*--|\bunion\s+select\b"),
        # destructive requests
        ("destructive", r"\b(drop|truncate)\b[^.?!]{0,30}\b(tables?|database|db|schema|columns?|indexe?s?)\b|\balter\s+table\b"),
        ("destructive", r"\b(delete|wipe|erase|destroy|purge|remove|clear)\b[^.?!]{0,30}\b(database|db|tables?|records|all|"
                        r"every\w*|entire|whole)\b"),
        ("destructive", r"\b(change|modify|update|edit|alter|set|increase|decrease|cut|adjust|delete|overwrite)\b[^.?!]{0,30}"
                        r"\b(payroll|salary|salaries|ctc|compensation|wages?|bank)\b"),
        # sensitive data (never available through the AI, for any role)
        ("sensitive_data", r"\b(pan|aadhaa?r|ssn|uan|ifsc)\b|\besi\s+(number|details|no)\b"),
        ("sensitive_data", r"\bbank\s*(accounts?|details|a/c|numbers?|info\w*)\b|\baccount numbers?\b"),
        ("sensitive_data", r"\b(passwords?|passcodes?|credentials?|jwt)\b|\b(api|secret) keys?\b|\b(access|auth) tokens?\b"),
        ("sensitive_data", r"\b(date of birth|dob)\b"),
        ("sensitive_data", r"\b(everyone'?s?|everybody'?s?|all|each|every|other|others'?|employees'?|their|his|her|team'?s?|"
                           r"[a-z]+'s)\b[^.?!]{0,25}\b(salary|salaries|ctc|compensation|wages?|pay ?slips?|payroll|net pay|"
                           r"gross pay)\b"),
    )
)


def refusal_category(message: str) -> str | None:
    for category, pattern in _REFUSAL_RULES:
        if pattern.search(message):
            return category
    return None


# --- 2. clause splitting --------------------------------------------------------------------

_SPLIT_RE = re.compile(r"\s*(?:[;?!]+|\.(?=\s|$)|\b(?:and then|and also|as well as|also|then|plus|and)\b)\s*", _I)


@dataclass(frozen=True, slots=True)
class Clause:
    start: int
    end: int
    intent: Intent | None
    weak: bool = False  # only a weak "tell me about" signal


def split_clauses(message: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    position = 0
    for match in _SPLIT_RE.finditer(message):
        if match.start() > position:
            spans.append((position, match.start()))
        position = match.end()
    if position < len(message):
        spans.append((position, len(message)))
    return [(start, end) for start, end in spans if message[start:end].strip(" ,.")]


# --- 3. per-clause signals ------------------------------------------------------------------

_POLITE = (r"^(?:(?:please|kindly|pls|can you|could you|would you|will you|i want to|i'd like to|i would like to|"
           r"i wish to|help me|let's|lets|go ahead and|just|now)\s+)*")

_VAGUE_RE = re.compile(rf"{_POLITE}(?:handle|deal with|take care of|sort out|sort|fix|process|action|look into|work on)\b|"
                       r"\bdo (?:something|anything|whatever)\b|\b(?:handle|deal with|take care of|sort out)\b", _I)
_WEAK_VAGUE_RE = re.compile(r"\b(?:tell me about|what about|info(?:rmation)? (?:on|about)|anything about|something about|"
                            r"help (?:me )?with)\b", _I)
_ACTION_RE = re.compile(rf"{_POLITE}(?:approve|reject|decline|deny|apply|submit|raise|create|assign|reassign|deactivate|"
                        r"publish|announce|mark|resolve|update|change|enroll|onboard|request|take)\b", _I)
_POLICY_RES = tuple(re.compile(pattern, _I) for pattern in (
    r"\bpolic(?:y|ies)\b",
    r"\b(?:rules?|guidelines?|handbook|entitle\w*|eligib\w*|procedures?|carry[- ]?forward|carry over|encash\w*|"
    r"notice period|probation|code of conduct|dress code|holiday list|working hours|cut-?off)\b",
    r"\b(?:can|may|could|should|must)\s+(?:i|we|employees?|staff|managers?|someone|anyone|an? (?:employee|manager|intern))\s+"
    r"(?:take|get|work|carry|apply|claim|use|avail|encash|combine|bring|request|approve|wear|join)\b",
    r"\b(?:am i|are we|are employees|is an? employee|are managers)\s+(?:allowed|entitled|eligible|permitted|required|supposed)\b",
    r"\bwhat happens (?:if|when)\b",
    r"\bhow (?:many|much|long)\b[^.?!]{0,40}\b(?:can|may|should|must)\s+(?:i|we|employees?|an? employee|staff)\b",
    # Entitlement wording ("How many sick leaves do I get?") is a policy question; "... do I have" stays a lookup.
    r"\bhow (?:many|much|long)\b[^.?!]{0,40}\b(?:do|does|will)\s+(?:i|we|employees?|an? employee|staff|managers?)\s+get\b",
    r"\b(?:work from home|wfh|remote work|late (?:attendance|arrival|coming|login)|maternity|paternity|bereavement)\b",
))
# Questions about who/which records: checked before policy wording, because names can contain "policy".
_RECORD_LOOKUP_RE = re.compile(
    r"^\s*(?:who\s+(?:is|are)\s+(?:assigned|working|on)\b|who\s+(?:works|reports)\b|"
    r"which\s+(?:employees?|people|projects?|tickets?)\b)", _I)
_SQL_RES = tuple(re.compile(pattern, _I) for pattern in (
    r"\bhow many\b|\bnumber of\b|\bcount\b|\btotal\b",
    r"^(?:list|show|display|fetch|find|get|give me)\b|\b(?:show|list)(?: me)? (?:all|my|our|the)\b",
    r"\bwho (?:is|are|was|reports|works|manages|has|have)\b|\bwhich (?:employees?|people|projects?|tickets?|departments?|"
    r"team|leaves?|requests?)\b",
    r"\b(?:remaining|balance|left|pending|outstanding|open|resolved|approved|rejected)\b",
    r"\breports? to\b|\bstatus of\b|\bhistory\b",
    r"\b(?:my|our)\s+(?:team|teams|projects?|tickets?|leaves?|leave (?:requests?|history|balance)|reports|direct reports|"
    r"manager|department|skills?|attendance|requests?)\b",
))


def clause_intent(text: str) -> tuple[Intent | None, bool]:
    """Deterministic intent of one clause: (intent or None, weak)."""
    stripped = text.strip()
    if _VAGUE_RE.search(stripped):
        return Intent.AMBIGUOUS, False
    if _ACTION_RE.search(stripped):
        return Intent.ACTION, False
    if _RECORD_LOOKUP_RE.search(stripped):  # "who is assigned to HR Policy Copilot?" is a lookup, not a policy question
        return Intent.SQL, False
    if any(pattern.search(stripped) for pattern in _POLICY_RES):
        return Intent.POLICY, False
    if any(pattern.search(stripped) for pattern in _SQL_RES):
        return Intent.SQL, False
    if _WEAK_VAGUE_RE.search(stripped):
        return Intent.AMBIGUOUS, True
    return None, False


def classify_clauses(message: str) -> list[Clause]:
    """Clauses with rule intents; clauses without a signal are merged into their neighbour."""
    raw = [(start, end, *clause_intent(message[start:end])) for start, end in split_clauses(message)]
    merged: list[Clause] = []
    pending_start: int | None = None
    for start, end, intent, weak in raw:
        if intent is None:
            if merged:
                previous = merged[-1]
                merged[-1] = Clause(previous.start, end, previous.intent, previous.weak)
            elif pending_start is None:
                pending_start = start
            continue
        merged.append(Clause(pending_start if pending_start is not None else start, end, intent, weak))
        pending_start = None
    if not merged and raw:
        merged.append(Clause(raw[0][0], raw[-1][1], None))
    # Adjacent read clauses with the same intent are one request ("how many in A and how many in B").
    # Two imperative ACTION clauses stay separate: they are two mutations and must not be merged.
    collapsed: list[Clause] = []
    for clause in merged:
        if (collapsed and collapsed[-1].intent == clause.intent and clause.intent is not None
                and clause.intent != Intent.ACTION):
            collapsed[-1] = Clause(collapsed[-1].start, clause.end, clause.intent, collapsed[-1].weak and clause.weak)
        else:
            collapsed.append(clause)
    return collapsed


# --- clarification texts --------------------------------------------------------------------

GENERIC_CLARIFICATION = (
    "I can answer HR policy questions, look up HR information you're allowed to see, or prepare an HR action "
    "(such as applying for or approving leave, or creating a ticket) for you to confirm. What would you like to do?"
)
_TOPIC_CLARIFICATIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bleaves?\b", _I), "Do you want to know the leave policy, check leave balances or requests, or apply for, "
                                     "approve or reject a leave request?"),
    (re.compile(r"\btickets?\b", _I), "Do you want to check ticket details, create a ticket, assign it, or update its status?"),
    (re.compile(r"\bprojects?\b", _I), "Do you want to see project information, create a project, or assign someone to a project?"),
)
ONE_ACTION_CLARIFICATION = "I can prepare one HR action at a time. Which action would you like to do first?"
TOO_MANY_CLARIFICATION = "That's several requests in one message. Please ask one or two things at a time."


def clarification_for(message: str) -> str:
    for pattern, text in _TOPIC_CLARIFICATIONS:
        if pattern.search(message):
            return text
    return GENERIC_CLARIFICATION


def _safe_clarification(candidate: str | None, message: str) -> str:
    """LLM clarification text is untrusted: redacted, length-capped, and replaced when it claims actions/roles."""
    if not candidate or contains_sensitive_value(candidate) or claims_action_or_role(candidate):
        return clarification_for(message)
    cleaned = redact(candidate, max_length=300).strip()
    return cleaned or clarification_for(message)


# --- classifier -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Decision:
    start: int
    end: int
    intent: Intent
    source: str  # rules | llm
    confidence: float
    clarification: str | None = None


class RouterClassifier:
    """Builds a RoutingPlan. The LLM factory is resolved lazily, only for clauses rules cannot decide."""

    def __init__(self, *, llm_factory: Callable[[], LLMProvider], min_confidence: float = MIN_LLM_CONFIDENCE):
        self._llm_factory = llm_factory
        self._min_confidence = min_confidence
        self.llm_calls = 0

    async def plan(self, message: str, context: ConversationContext | None = None) -> RoutingPlan:
        # Refusal rules only ever look at the CURRENT message (history is data, not a trigger or a pass).
        category = refusal_category(message)
        if category is not None:
            return RoutingPlan(outcome="refuse", intents=(Intent.UNSUPPORTED,), reason=f"rules:{category}")
        if is_confirmation_attempt(message, context):
            return RoutingPlan(outcome="clarify", intents=(Intent.AMBIGUOUS,), reason="rules:confirmation_in_chat",
                               clarification_question=CONFIRM_IN_CHAT_MESSAGE)

        clauses = classify_clauses(message)
        if len(clauses) <= 1 and is_follow_up(message, context):
            return await self._plan_follow_up(message, clauses[0] if clauses else None, context)
        if not clauses:
            return RoutingPlan(outcome="clarify", intents=(Intent.AMBIGUOUS,), reason="rules:empty",
                               clarification_question=GENERIC_CLARIFICATION)
        if len(clauses) > MAX_CLAUSES:
            return RoutingPlan(outcome="clarify", intents=(Intent.AMBIGUOUS,), reason="rules:too_many_requests",
                               clarification_question=TOO_MANY_CLARIFICATION)

        decisions: list[_Decision] = []
        for clause in clauses:
            if clause.intent is not None:
                decisions.append(_Decision(clause.start, clause.end, clause.intent, "rules", 1.0 if not clause.weak else 0.5))
            else:
                decisions.append(await self._classify_with_llm(message, clause, context))
        return self._build_plan(message, decisions)

    async def _plan_follow_up(self, message: str, clause: Clause | None, context: ConversationContext) -> RoutingPlan:
        """One request that depends on the previous turn. The route is chosen from the current message's own
        signal, else the previous route, else the LLM; the forwarded question is user text only."""
        intent, source, confidence = (clause.intent if clause else None), "rules", 1.0
        if intent == Intent.AMBIGUOUS and clause is not None and not clause.weak:
            return RoutingPlan(outcome="clarify", intents=(Intent.AMBIGUOUS,), reason="rules:ambiguous",
                               clarification_question=clarification_for(message), follow_up=True)
        if intent not in CAPABILITY_ORDER:
            if context.anchor_route in {i.value for i in CAPABILITY_ORDER}:
                intent, source = Intent(context.anchor_route), "context"
            else:
                span = clause or Clause(0, len(message), None)
                decision = await self._classify_with_llm(message, span, context)
                intent, source, confidence = decision.intent, "llm", decision.confidence
                if intent == Intent.UNSUPPORTED:
                    return RoutingPlan(outcome="refuse", intents=(Intent.UNSUPPORTED,), source="llm",
                                       reason="llm:unsupported", confidence=confidence, follow_up=True)
                if intent == Intent.AMBIGUOUS:
                    return RoutingPlan(outcome="clarify", intents=(Intent.AMBIGUOUS,), source="llm", reason="llm:ambiguous",
                                       confidence=confidence, follow_up=True,
                                       clarification_question=_safe_clarification(decision.clarification, message))
        question = contextual_question(context.anchor_question, message)
        return RoutingPlan(outcome="route", intents=(intent,), steps=(PlanStep(intent, question),), source=source,
                           reason=f"{source}:follow_up_{intent.value.lower()}", confidence=confidence, follow_up=True)

    async def _classify_with_llm(self, message: str, clause: Clause, context: ConversationContext | None = None) -> _Decision:
        text = message[clause.start:clause.end].strip()
        try:
            self.llm_calls += 1
            result = await observe(self._llm_factory(), "route_classification").generate_structured(
                [LLMMessage(role="user", content=build_router_message(text, context))],
                RouterClassification,
                system=ROUTER_SYSTEM_PROMPT,
            )
        except AIUnavailableError:
            raise
        except (AIInvalidOutputError, AIProviderError) as exc:
            logger.warning("ai.router.classification_failed", error=type(exc).__name__)
            raise AIClassificationError() from exc
        except Exception as exc:  # unexpected provider failure: fail closed, never guess a route
            logger.error("ai.router.classification_failed", error=type(exc).__name__)
            raise AIClassificationError() from exc
        if not isinstance(result, RouterClassification):  # defensive: a provider must return the schema
            raise AIClassificationError()
        intent = Intent(result.intent)
        if intent in CAPABILITY_ORDER and result.confidence < self._min_confidence:
            intent = Intent.AMBIGUOUS
        return _Decision(clause.start, clause.end, intent, "llm", result.confidence, result.clarification_question)

    def _build_plan(self, message: str, decisions: list[_Decision]) -> RoutingPlan:
        sources = {decision.source for decision in decisions}
        source = "mixed" if len(sources) > 1 else next(iter(sources))
        confidence = min(decision.confidence for decision in decisions)
        intents = tuple(dict.fromkeys(decision.intent for decision in decisions))

        if Intent.UNSUPPORTED in intents:
            return RoutingPlan(outcome="refuse", intents=(Intent.UNSUPPORTED,), source=source, reason=f"{source}:unsupported",
                               confidence=confidence)
        if Intent.AMBIGUOUS in intents:
            llm_question = next((d.clarification for d in decisions if d.intent == Intent.AMBIGUOUS and d.clarification), None)
            return RoutingPlan(outcome="clarify", intents=(Intent.AMBIGUOUS,), source=source, reason=f"{source}:ambiguous",
                               confidence=confidence, clarification_question=_safe_clarification(llm_question, message))
        if sum(1 for decision in decisions if decision.intent == Intent.ACTION) > 1:
            return RoutingPlan(outcome="clarify", intents=(Intent.AMBIGUOUS,), source=source, reason=f"{source}:multiple_actions",
                               confidence=confidence, clarification_question=ONE_ACTION_CLARIFICATION)

        ordered = tuple(intent for intent in CAPABILITY_ORDER if intent in intents)
        if len(ordered) == 1:
            steps = (PlanStep(ordered[0], message),)
        else:
            steps = tuple(
                PlanStep(intent, " and ".join(message[d.start:d.end].strip(" ,") for d in decisions if d.intent == intent))
                for intent in ordered
            )
        reason = f"{source}:" + "+".join(intent.value.lower() for intent in ordered)
        return RoutingPlan(outcome="route", intents=ordered, steps=steps, source=source, reason=reason, confidence=confidence)


def get_router_classifier() -> RouterClassifier:
    """FastAPI dependency. The fast model (AI_LLM_FAST_MODEL) is resolved only if rules are inconclusive."""
    from app.services.ai.providers import get_fast_llm_provider

    return RouterClassifier(llm_factory=get_fast_llm_provider)
