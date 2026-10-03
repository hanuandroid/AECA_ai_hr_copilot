"""Conversation fixtures. Reuses the Phase 6 router fixtures (real services, scripted LLM, Rahul/John data)."""

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select

from app.models.ai_conversation import AIConversation, AIConversationMessage
from app.services.ai.conversation.chat import ConversationalCopilot
from app.services.ai.conversation.service import ConversationService
from app.services.ai.orchestration.models import RouterClassification
from app.services.ai.rag.models import PolicyLLMOutput
from app.services.ai.sql.models import SQLFormatOutput, SQLGenerationOutput
from app.services.ai.actions.models import ActionProposal
from tests.ai.orchestration.conftest import (  # noqa: F401  (fixtures re-exported for this package)
    JOHN,
    RAHUL,
    RAHUL_LEAVE,
    RAHUL_OUTSIDE_LEAVE,
    MultiLLM,
    action_proposal,
    classification,
    ctx_for,
    ingested,
    install_real,
    policy_output,
    real_copilot,
    router_data,
)

POLICY_ANSWER = "This is covered by the relevant HR policy."  # no numbers: the RAG guard rejects unsupported numbers
MAX_MESSAGES = 200
CONTEXT_MESSAGES = 6
CONTEXT_CHARS = 2000
STORED_CHARS = 4000


def by_prompt(*rules: tuple[str, Any], default: Any = None) -> Callable[[str], Any]:
    """Scripted model output chosen by a (case-insensitive) substring of the prompt; last match wins so a
    follow-up prompt (which also contains the earlier question) picks the more specific rule listed later."""

    def pick(prompt: str) -> Any:
        # Match only the user's question (the prompts also contain schema/policy text, e.g. "payroll").
        lowered, chosen = _question_part(prompt).lower(), default
        for needle, payload in rules:
            if needle.lower() in lowered:
                chosen = payload
        if chosen is None:
            raise ValueError("no scripted output")
        return chosen

    return pick


def _question_part(prompt: str) -> str:
    for start, end in (("<question>", "</question>"), ("<user_question>", "</user_question>"), ("<request>", "</request>")):
        if start in prompt and end in prompt:
            return prompt.split(start, 1)[1].split(end, 1)[0]
    return prompt


def sql(statement: str) -> dict[str, str]:
    return {"sql": statement, "explanation": "test"}


def script(*, router: Any = None, sql_rules: Any = None, action: Any = None, policy: Any = None,
           answer: str = "Here are the results.") -> MultiLLM:
    payloads: dict[type, Any] = {PolicyLLMOutput: policy or policy_output(POLICY_ANSWER), SQLFormatOutput: {"answer": answer}}
    if router is not None:
        payloads[RouterClassification] = router
    if sql_rules is not None:
        payloads[SQLGenerationOutput] = sql_rules
    if action is not None:
        payloads[ActionProposal] = action
    return MultiLLM(payloads)


def make_conversations(db, **overrides) -> ConversationService:
    options = {"max_messages": MAX_MESSAGES, "context_messages": CONTEXT_MESSAGES, "context_chars": CONTEXT_CHARS,
               "stored_message_chars": STORED_CHARS, **overrides}
    return ConversationService(db, **options)


@pytest.fixture
def conversations(db) -> ConversationService:
    return make_conversations(db)


@pytest.fixture
def chat_service(db, real_copilot) -> Callable:
    """chat_service(llm, **conversation_overrides) -> ConversationalCopilot over the REAL services."""

    def _make(llm: MultiLLM, **overrides) -> ConversationalCopilot:
        return ConversationalCopilot(conversations=make_conversations(db, **overrides), copilot=real_copilot(llm))

    return _make


async def stored_messages(db, public_id: str) -> list[AIConversationMessage]:
    db.expire_all()
    conversation = (await db.execute(select(AIConversation).where(AIConversation.public_id == public_id))).scalar_one()
    return list((await db.execute(select(AIConversationMessage).where(
        AIConversationMessage.conversation_id == conversation.id).order_by(AIConversationMessage.sequence))).scalars())
