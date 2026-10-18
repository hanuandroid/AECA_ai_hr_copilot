from fastapi import status


class AIError(Exception):
    """Base AI error. `message` is always safe to show to end users."""

    code: str = "AI_ERROR"
    message: str = "The AI assistant could not complete this request."
    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, message: str | None = None, *, code: str | None = None, detail: str | None = None):
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        # Internal reason for logs/tests only; never returned to clients.
        self.detail = detail
        super().__init__(self.message)


class AIUnavailableError(AIError):
    code = "AI_UNAVAILABLE"
    message = "The AI assistant is not available right now."
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE


class AIProviderError(AIError):
    code = "AI_PROVIDER_ERROR"
    message = "The AI provider failed to respond. Please try again."
    http_status = status.HTTP_502_BAD_GATEWAY


class AIInvalidOutputError(AIError):
    code = "AI_INVALID_OUTPUT"
    message = "The AI assistant produced an invalid response. Please rephrase and try again."
    http_status = status.HTTP_502_BAD_GATEWAY


class AIProviderPolicyError(AIError):
    """The provider boundary refused to send a payload (unregistered operation, or preparing it failed)."""

    code = "AI_PROVIDER_POLICY_BLOCKED"
    message = "The AI assistant could not process this request safely."
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR


class AIInvalidRequestError(AIError):
    code = "AI_INVALID_REQUEST"
    message = "The request is not valid."
    http_status = status.HTTP_400_BAD_REQUEST


class AIEmbeddingError(AIError):
    code = "AI_EMBEDDING_FAILED"
    message = "The assistant could not process this question right now. Please try again."
    http_status = status.HTTP_502_BAD_GATEWAY


class AIRetrievalError(AIError):
    code = "AI_RETRIEVAL_FAILED"
    message = "The assistant could not search the policy library right now. Please try again."
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR


class AIPermissionDeniedError(AIError):
    code = "AI_PERMISSION_DENIED"
    message = "You do not have permission to perform this action."
    http_status = status.HTTP_403_FORBIDDEN


class AIInactiveUserError(AIPermissionDeniedError):
    code = "AI_USER_INACTIVE"
    message = "Your account is not active, so the AI assistant is unavailable."


# --- Action agent -----------------------------------------------------------------------------


class AITargetNotFoundError(AIError):
    """Same message whether the target does not exist or is outside the caller's scope (no oracle)."""

    code = "AI_TARGET_NOT_FOUND"
    message = "I couldn't find a matching record that you're allowed to act on."
    http_status = status.HTTP_404_NOT_FOUND


class AIConfirmationInvalidError(AIError):
    """Same message for unknown, foreign, used, expired or tampered confirmations."""

    code = "AI_CONFIRMATION_INVALID"
    message = "This confirmation is invalid or has expired. Please ask again."
    http_status = status.HTTP_400_BAD_REQUEST


class AIBackendUnavailableError(AIError):
    code = "AI_BACKEND_UNAVAILABLE"
    message = "The HR system could not complete this action right now. Please try again later."
    http_status = status.HTTP_502_BAD_GATEWAY


class AIRateLimitedError(AIError):
    """Per-user AI request rate exceeded (production hardening B-09). Raised before any model call."""

    code = "AI_RATE_LIMITED"
    message = "You're sending requests too quickly. Please wait a minute and try again."
    http_status = status.HTTP_429_TOO_MANY_REQUESTS


class AIBudgetExceededError(AIError):
    """Per-user daily token (or configured cost) budget reached (B-09). Raised before any model call."""

    code = "AI_BUDGET_EXCEEDED"
    message = "You've reached today's AI usage limit. Please try again tomorrow."
    http_status = status.HTTP_429_TOO_MANY_REQUESTS


class AIBusyError(AIError):
    """All model-call slots stayed busy for the queue timeout (B-09 concurrency bound)."""

    code = "AI_BUSY"
    message = "The Copilot is busy right now. Please try again shortly."
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE


class AIActionOutcomeUnknownError(AIError):
    """The business call did not finish within the execution deadline: it may or may not have been applied.
    Never retried automatically (business APIs have no idempotency keys)."""

    code = "AI_ACTION_OUTCOME_UNKNOWN"
    message = "We couldn't confirm whether this action completed. Please check the related page before trying again."
    http_status = status.HTTP_504_GATEWAY_TIMEOUT


# --- Orchestration (copilot router) --------------------------------------------------------------


class AIClassificationError(AIError):
    code = "AI_CLASSIFICATION_FAILED"
    message = "The assistant could not understand this request. Please rephrase it."
    http_status = status.HTTP_502_BAD_GATEWAY


class AIRoutingError(AIError):
    code = "AI_ROUTING_FAILED"
    message = "The assistant could not route this request. Please try again or rephrase it."
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR


class AIMaxIterationsError(AIError):
    code = "AI_MAX_ITERATIONS"
    message = "The assistant stopped because this request needed too many steps. Please ask one thing at a time."
    http_status = status.HTTP_422_UNPROCESSABLE_CONTENT


# --- Audit ------------------------------------------------------------------------------------


class AIAuditUnavailableError(AIError):
    """Only raised before executing a confirmed action whose audit record cannot be written (fail-closed)."""

    code = "AI_AUDIT_UNAVAILABLE"
    message = "The assistant cannot complete this action right now because it could not be recorded. Please try again."
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE


# --- Conversations ------------------------------------------------------------------------------


class AIConversationNotFoundError(AIError):
    """Same response for unknown, malformed, archived or another user's conversation (no oracle)."""

    code = "AI_CONVERSATION_NOT_FOUND"
    message = "Conversation not found."
    http_status = status.HTTP_404_NOT_FOUND


class AIConversationLimitError(AIError):
    code = "AI_CONVERSATION_LIMIT_REACHED"
    message = "This conversation has reached its message limit. Please start a new conversation."
    http_status = status.HTTP_409_CONFLICT


# --- SQL agent rejections (messages never include SQL, table names, values or target ids) -----


class AISQLError(AIError):
    code = "SQL_NOT_ALLOWED"
    message = "That query uses an operation the assistant is not allowed to run."
    http_status = status.HTTP_403_FORBIDDEN


class SQLNotAllowedError(AISQLError):
    pass


class SQLParseError(AISQLError):
    code = "SQL_PARSE_ERROR"
    message = "The assistant could not build a valid query for that question. Please rephrase it."
    http_status = status.HTTP_422_UNPROCESSABLE_CONTENT


class SQLMutationRejectedError(AISQLError):
    code = "SQL_MUTATION_REJECTED"
    message = "The assistant can only read data. It cannot create, change or delete records."


class SQLTableNotAllowedError(AISQLError):
    code = "SQL_TABLE_NOT_ALLOWED"
    message = "That information is not available through the assistant."


class SQLColumnNotAllowedError(AISQLError):
    code = "SQL_COLUMN_NOT_ALLOWED"
    message = "That information is not available through the assistant."


class SQLScopeViolationError(AISQLError):
    code = "SQL_SCOPE_VIOLATION"
    message = "You can only ask about data within your authorized scope: your own records, or your team's if you manage a team."


class SQLQueryTooComplexError(AISQLError):
    code = "SQL_QUERY_TOO_COMPLEX"
    message = "That question needs a query that is too complex. Please ask something more specific."
    http_status = status.HTTP_422_UNPROCESSABLE_CONTENT


class SQLExecutionError(AISQLError):
    code = "SQL_EXECUTION_ERROR"
    message = "The assistant could not run the query. Please try again."
    http_status = status.HTTP_500_INTERNAL_SERVER_ERROR


def ai_error_response(exc: AIError):
    """Standard envelope for AI errors; only the user-safe code/message are exposed."""
    from fastapi.responses import JSONResponse

    from app.core.response import error_response

    return JSONResponse(status_code=exc.http_status, content=error_response(exc.code, exc.message))
