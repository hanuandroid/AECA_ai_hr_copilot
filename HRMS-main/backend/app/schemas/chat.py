from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ChatSessionCreate(BaseModel):
    title: str = Field(min_length=3, max_length=120)


class ChatMessageCreate(BaseModel):
    role: str = Field(min_length=3, max_length=20)
    content: str = Field(min_length=1, max_length=4000)


class PolicyQuestionRequest(BaseModel):
    """POST /chat/policy body. Accepts `question` (Phase 3 spec) or `message` (assignment example).

    Emptiness/length are validated by the RAG service so errors use the AI error envelope.
    """

    question: str | None = Field(default=None, validation_alias=AliasChoices("question", "message"))


class ActionRequest(BaseModel):
    """POST /chat/actions body (`question`, or `message` as in the assignment)."""

    question: str | None = Field(default=None, validation_alias=AliasChoices("question", "message"))


class ActionConfirmRequest(BaseModel):
    """POST /chat/actions/confirm: ONLY the server-issued confirmation id. Any other field (action,
    employee_id, parameters...) is rejected - the server recovers the action from its own record."""

    model_config = ConfigDict(extra="forbid")

    confirmation_id: str = Field(min_length=1, max_length=300)


class SQLQuestionRequest(BaseModel):
    """POST /chat/sql body (`question`, or `message` as in the assignment)."""

    question: str | None = Field(default=None, validation_alias=AliasChoices("question", "message"))


class CopilotRequest(BaseModel):
    """POST /chat (unified copilot): the user's message and, for a follow-up, the conversation id.
    Identity, role and permissions come from the authenticated request; any other field (user_id, role,
    permissions...) is rejected. The conversation id is only looked up within the caller's own conversations
    (a malformed id is answered exactly like an unknown one)."""

    model_config = ConfigDict(extra="forbid")

    message: str | None = Field(default=None, validation_alias=AliasChoices("message", "question"))
    conversation_id: str | None = Field(default=None, max_length=64)
