from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class SQLGenerationOutput(BaseModel):
    """The ONLY fields accepted from the SQL-generating model. Extra fields (e.g. "authorized",
    "role", "employee_id") make the output invalid - nothing the model says is an authorization signal."""

    model_config = ConfigDict(extra="forbid")

    sql: str
    explanation: str


class SQLFormatOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str


class SQLAnswer(BaseModel):
    answer: str
    scope: str  # SELF | TEAM | ORG - the scope the rows were restricted to
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    sql: str | None = None  # only for roles with SQL_VIEW_RAW


@dataclass
class SQLRunRecord:
    """Structured, redaction-safe record of one SQL-agent request (observability-ready; no row values)."""

    employee_id: int
    role: str
    capability: str
    question_chars: int = 0
    request_id: str = field(default_factory=lambda: uuid4().hex)
    sql_hash: str | None = None
    scope: str | None = None
    tables: list[str] = field(default_factory=list)
    outcome: str = "pending"
    rejection_detail: str | None = None
    duration_ms: int | None = None
    row_count: int | None = None
    llm_calls: int = 0
