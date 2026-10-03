"""Owner-only API views of conversations. Internal ids, owner ids and resolved questions are not exposed."""

from datetime import datetime

from pydantic import BaseModel

from app.models.ai_conversation import AIConversation, AIConversationMessage


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, conversation: AIConversation) -> "ConversationSummary":
        return cls(conversation_id=conversation.public_id, title=conversation.title,
                   message_count=conversation.message_count, created_at=conversation.created_at,
                   updated_at=conversation.updated_at)


class ConversationMessageView(BaseModel):
    sequence: int
    role: str
    content: str
    route: str | None = None
    status: str | None = None
    error_code: str | None = None
    created_at: datetime

    @classmethod
    def of(cls, message: AIConversationMessage) -> "ConversationMessageView":
        return cls(sequence=message.sequence, role=message.role, content=message.content, route=message.route,
                   status=message.status, error_code=message.error_code, created_at=message.created_at)


class ConversationDetail(ConversationSummary):
    messages: list[ConversationMessageView]
