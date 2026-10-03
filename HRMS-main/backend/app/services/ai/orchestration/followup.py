"""Follow-up context for the copilot router (Phase 7). DB-free: built by the conversation service.

Conversation history is UNTRUSTED context used only to resolve references ("it", "them", "that
policy", an answer to a clarification). It never carries identity, role, scope or permissions, and
it is never a source of truth:
- only earlier USER text (the anchor question) is ever forwarded to a specialised service, so a
  previous assistant answer (a policy statement, a count, "you are admin") never reaches RAG, SQL
  or the action agent;
- assistant text is shown only to the classifier LLM, inside delimiters, as data;
- refusal rules always run on the current message, so a benign follow-up is not refused because of
  something said earlier, and an earlier refused message is never used as an anchor.
"""

import re
from dataclasses import dataclass
from typing import Literal

_I = re.IGNORECASE

MAX_SERVICE_QUESTION_CHARS = 1000  # same bound the specialised services validate
MAX_ANCHOR_CHARS = 600
FOLLOW_UP_SEPARATOR = "\n"  # plain sentences: a label such as "Follow-up:" measurably hurts RAG retrieval

CONFIRM_IN_CHAT_MESSAGE = (
    "I can't confirm actions from a chat message. To go ahead, confirm the pending action with its Confirm "
    "button; otherwise just ask your next question."
)


@dataclass(frozen=True, slots=True)
class HistoryMessage:
    role: Literal["USER", "ASSISTANT"]
    text: str  # already redacted and length-capped


@dataclass(frozen=True, slots=True)
class ConversationContext:
    """Bounded, untrusted view of the recent conversation (oldest first)."""

    recent: tuple[HistoryMessage, ...] = ()
    anchor_question: str | None = None  # the latest answered USER question (resolved form), user text only
    anchor_route: str | None = None  # POLICY | SQL | ACTION when that turn used exactly one capability
    awaiting_clarification: bool = False  # the latest assistant turn asked a clarification question
    awaiting_confirmation: bool = False  # the latest assistant turn proposed an action

    @property
    def is_empty(self) -> bool:
        return not self.recent


_REFERENCE_RE = re.compile(
    r"\b(?:it|its|that|this|those|these|them|they|their|theirs|he|him|his|she|her|hers|same|"
    r"the one|the other|the first|the second|the last|what about|how about|and for|as well)\b", _I)
_TOPIC_RE = re.compile(
    r"\b(?:polic(?:y|ies)|leaves?|tickets?|projects?|employees?|team|announcements?|salary|payroll|balance|"
    r"attendance|wfh|work from home|reports?|department|managers?|holidays?)\b", _I)
_CONFIRM_RE = re.compile(r"^\s*(?:please\s+|i\s+)?confirm\b", _I)
_AFFIRMATION_RE = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|ok|okay|sure|go ahead|proceed|do it|please do|approve it|sounds good)\b[\s\w,.!]*$", _I)
MAX_FOLLOW_UP_WORDS = 6


def is_follow_up(message: str, context: ConversationContext | None) -> bool:
    """Deterministic: does this message depend on the previous turn?"""
    if context is None or not context.anchor_question:
        return False
    if context.awaiting_clarification:
        return True  # the user is answering the assistant's question
    if _REFERENCE_RE.search(message):
        return True
    return len(message.split()) <= MAX_FOLLOW_UP_WORDS and not _TOPIC_RE.search(message)


def looks_like_confirmation(message: str) -> bool:
    """Context-free check used to keep "yes"/"confirm" turns out of the follow-up anchor."""
    return bool((_CONFIRM_RE.match(message) and len(message.split()) <= 12) or _AFFIRMATION_RE.match(message))


def is_confirmation_attempt(message: str, context: ConversationContext | None) -> bool:
    """"Yes" after a proposal, or any "confirm ..." message: confirmation is never done through chat."""
    if _CONFIRM_RE.match(message) and len(message.split()) <= 12:
        return True
    return bool(context and context.awaiting_confirmation and _AFFIRMATION_RE.match(message))


def contextual_question(anchor: str | None, current: str, limit: int = MAX_SERVICE_QUESTION_CHARS) -> str:
    """The text forwarded to a specialised service: the earlier USER question, then the follow-up, as two
    plain sentences separated by a newline (e.g. the WFH question, then "Does that apply to managers?").

    Bounded: the anchor keeps its most recent part, and the whole never exceeds `limit`.
    """
    if not anchor:
        return current
    room = min(MAX_ANCHOR_CHARS, limit - len(current) - len(FOLLOW_UP_SEPARATOR))
    if room < 20:
        return current
    anchor = anchor.strip()
    if anchor[-1:] not in ".?!":
        anchor += "."
    if len(anchor) > room:
        anchor = "…" + anchor[-(room - 1):]
    return f"{anchor}{FOLLOW_UP_SEPARATOR}{current}"


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_history(context: ConversationContext | None) -> str:
    """Delimited history block for the classifier LLM (empty when there is no history)."""
    if context is None or context.is_empty:
        return ""
    lines = "\n".join(f"{'User' if m.role == 'USER' else 'Assistant'}: {escape(m.text)}" for m in context.recent)
    return f"<conversation_context>\n{lines}\n</conversation_context>\n"
