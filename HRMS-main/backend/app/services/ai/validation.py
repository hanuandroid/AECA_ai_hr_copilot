"""Shared validation of end-user questions for every AI capability."""

import re

from app.services.ai.errors import AIInvalidRequestError

MAX_QUESTION_CHARS = 1000
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def validate_question(question: object) -> str:
    if not isinstance(question, str):
        raise AIInvalidRequestError("Please provide a question.")
    cleaned = " ".join(_CONTROL_CHARS_RE.sub(" ", question).split())
    if not cleaned:
        raise AIInvalidRequestError("Please enter a question.")
    if len(cleaned) > MAX_QUESTION_CHARS:
        raise AIInvalidRequestError(f"Your question is too long. Please keep it under {MAX_QUESTION_CHARS} characters.")
    return cleaned
