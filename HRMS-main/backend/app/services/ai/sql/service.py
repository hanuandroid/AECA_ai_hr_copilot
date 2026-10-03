"""SQLAgentService: authorized, validated, scope-enforced, read-only Text-to-SQL.

    run_authorized(SQL_READ_SELF)           <- first; inactive/unauthorized users never reach the LLM
    validate_question
    LLM -> {sql, explanation}               (untrusted generator, structured output)
    parse_select                            (one read-only statement, AST deny-list)
    validate_generated_sql                  (tables, columns, stars, joins, functions, ids, scope intent)
    enforce_scope + LIMIT + verify_final    (backend scope rewrite, re-validated)
    ReadOnlySQLExecutor                     (mode=ro, query_only, authorizer, timeout, row cap)
    redact_rows -> format_answer            (only redacted rows reach the formatter)

The LLM is never an authorization authority, and SQL validation is enforced independently of the
LLM prompt.
"""

import hashlib
import time
from collections.abc import Callable
from datetime import date

import structlog

from app.core.config import settings
from app.services.ai.audit.events import AuditEventType, AuditStatus
from app.services.ai.audit.provider import observe
from app.services.ai.audit.recorder import Timer, record
from app.services.ai.authorization import AIUserContext, Capability, grant_for, run_authorized, sql_read_scope
from app.services.ai.errors import AIError, AISQLError
from app.services.ai.llm.base import LLMProvider
from app.services.ai.providers import get_llm_provider
from app.services.ai.redaction import redact
from app.services.ai.sql.executor import ReadOnlySQLExecutor, sqlite_path_from_url
from app.services.ai.sql.formatter import format_answer, redact_rows
from app.services.ai.sql.generator import SQLGenerator
from app.services.ai.sql.models import SQLAnswer, SQLRunRecord
from app.services.ai.sql.parser import DIALECT, parse_select
from app.services.ai.sql.scope import enforce_scope
from app.services.ai.sql.validator import validate_generated_sql
from app.services.ai.validation import validate_question

logger = structlog.get_logger()


class SQLAgentService:
    def __init__(
        self,
        *,
        llm_factory: Callable[[], LLMProvider],
        executor_factory: Callable[[], ReadOnlySQLExecutor],
        max_rows: int,
        max_joins: int,
        today: Callable[[], date] = date.today,
    ):
        self._llm_factory = llm_factory
        self._executor_factory = executor_factory
        self._max_rows = max_rows
        self._max_joins = max_joins
        self._today = today
        self.last_record: SQLRunRecord | None = None  # for tests/observability hooks

    async def answer(self, ctx: AIUserContext, question: object) -> SQLAnswer:
        record(AuditEventType.SQL_REQUEST, AuditStatus.STARTED, route="SQL")
        timer = Timer()
        self.last_record = None
        try:
            result = await run_authorized(ctx, Capability.SQL_READ_SELF, lambda: self._answer(ctx, question))
        except AIError as error:
            self._audit_failure(error, timer.ms)
            raise
        run = self.last_record
        record(AuditEventType.SQL_RESPONSE, AuditStatus.SUCCESS, route="SQL", duration_ms=timer.ms, query_type="SELECT",
               tables=run.tables if run else None, scope=result.scope, row_count=result.row_count,
               truncated=result.truncated, security_result="ALLOWED")
        return result

    def _audit_failure(self, error: AIError, duration_ms: float) -> None:
        """Never the SQL text or values: only the safe code and the validated table names (if any)."""
        tables = self.last_record.tables if self.last_record and self.last_record.tables else None
        if isinstance(error, AISQLError) and error.code not in ("SQL_PARSE_ERROR", "SQL_EXECUTION_ERROR"):
            record(AuditEventType.SQL_SECURITY_REJECTION, AuditStatus.DENIED, route="SQL", duration_ms=duration_ms,
                   error_code=error.code, security_result=error.code, tables=tables)
        else:
            record(AuditEventType.SQL_RESPONSE, AuditStatus.FAILED, route="SQL", duration_ms=duration_ms,
                   error_code=error.code, tables=tables)

    async def _answer(self, ctx: AIUserContext, question: object) -> SQLAnswer:
        record = SQLRunRecord(employee_id=ctx.employee_id, role=ctx.role.value, capability=Capability.SQL_READ_SELF.value)
        self.last_record = record
        started = time.monotonic()
        try:
            cleaned = validate_question(question)
            record.question_chars = len(cleaned)
            scope = sql_read_scope(ctx)
            record.scope = scope.level.value

            llm = observe(self._llm_factory(), "sql")
            generated = await SQLGenerator(llm).generate(cleaned, ctx, scope, self._today())
            record.llm_calls += 1
            record.sql_hash = hashlib.sha256(generated.sql.encode("utf-8")).hexdigest()[:16]

            validated = validate_generated_sql(parse_select(generated.sql), ctx, max_joins=self._max_joins)
            record.tables = sorted(validated.tables)
            executable = enforce_scope(validated, ctx, max_rows=self._max_rows, max_joins=self._max_joins)

            result = await self._executor_factory().execute(
                executable.sql, executable.params, allowed_tables=executable.tables
            )
            columns, rows = redact_rows(result.columns, result.rows)
            record.row_count = len(rows)

            answer, used_llm = await format_answer(llm, cleaned, columns, rows, result.truncated)
            record.llm_calls += int(used_llm)
            record.outcome = "ok"
            return SQLAnswer(
                answer=answer,
                scope=scope.level.value,
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=result.truncated,
                sql=validated.tree.sql(dialect=DIALECT, comments=False) if grant_for(ctx.role, Capability.SQL_VIEW_RAW) else None,
            )
        except AIError as exc:
            record.outcome = exc.code
            record.rejection_detail = redact(exc.detail, max_length=200) if exc.detail else None
            raise
        finally:
            record.duration_ms = int((time.monotonic() - started) * 1000)
            logger.info("ai.sql.run", **{k: v for k, v in vars(record).items()})


def get_sql_agent_service() -> SQLAgentService:
    """FastAPI dependency. Nothing is resolved until after authorization (factories are lazy)."""
    return SQLAgentService(
        llm_factory=get_llm_provider,
        executor_factory=lambda: ReadOnlySQLExecutor(
            sqlite_path_from_url(settings.database_url),
            timeout_seconds=settings.ai_sql_timeout_seconds,
            max_rows=settings.ai_sql_max_rows,
        ),
        max_rows=settings.ai_sql_max_rows,
        max_joins=settings.ai_sql_max_joins,
    )
