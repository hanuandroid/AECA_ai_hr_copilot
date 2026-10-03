"""Result redaction + natural-language formatting.

DB rows -> redaction (drop sensitive-named columns, redact sensitive-looking string values)
-> LLM formatter (sees only the redacted rows) -> deterministic guard -> answer.
If the formatter output is invalid, unsupported (numbers not in the result) or unsafe, a
deterministic summary is used instead, so formatting can never invent data.
"""

import re
from typing import Any

import structlog

from app.services.ai.authorization.sensitive import is_sensitive_field
from app.services.ai.errors import AIError
from app.services.ai.llm.base import LLMMessage, LLMProvider
from app.services.ai.rag.guard import claims_action_or_role
from app.services.ai.redaction import contains_sensitive_value, redact
from app.services.ai.sql.models import SQLFormatOutput
from app.services.ai.sql.prompts import FORMAT_SYSTEM_PROMPT, build_format_message

logger = structlog.get_logger()
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def redact_rows(columns: list[str], rows: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    safe_columns = [column for column in columns if not is_sensitive_field(column)]
    safe_rows = [
        {column: (redact(value, max_length=1000) if isinstance(value, str) else value) for column, value in row.items() if column in safe_columns}
        for row in rows
    ]
    return safe_columns, safe_rows


def deterministic_summary(rows: list[dict[str, Any]], truncated: bool) -> str:
    if not rows:
        return "No matching records were found within your access scope."
    if len(rows) == 1 and len(rows[0]) == 1:
        (value,) = rows[0].values()
        return f"The result is {value}."
    summary = f"Found {len(rows)} matching record{'s' if len(rows) != 1 else ''}."
    return summary + (" Only the first results are shown." if truncated else "")


def is_supported_answer(answer: str, question: str, rows: list[dict[str, Any]]) -> bool:
    if not answer.strip() or contains_sensitive_value(answer) or claims_action_or_role(answer):
        return False
    evidence = " ".join(str(value) for row in rows for value in row.values()) + f" {len(rows)} {question}"
    return set(_NUMBER_RE.findall(answer)) <= set(_NUMBER_RE.findall(evidence))


async def format_answer(
    llm: LLMProvider, question: str, columns: list[str], rows: list[dict[str, Any]], truncated: bool
) -> tuple[str, bool]:
    """Returns (answer, used_llm). Only redacted rows ever reach the model."""
    if not rows:
        return deterministic_summary(rows, truncated), False
    try:
        output = await llm.generate_structured(
            [LLMMessage(role="user", content=build_format_message(question, columns, rows, truncated))],
            SQLFormatOutput,
            system=FORMAT_SYSTEM_PROMPT,
        )
    except AIError as exc:
        logger.warning("ai.sql.format_failed", code=exc.code)
        return deterministic_summary(rows, truncated), True
    if not is_supported_answer(output.answer, question, rows):
        logger.warning("ai.sql.format_rejected")
        return deterministic_summary(rows, truncated), True
    return output.answer.strip(), True
