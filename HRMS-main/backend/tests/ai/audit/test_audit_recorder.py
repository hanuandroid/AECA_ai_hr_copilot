"""Recorder, typed metadata, provider observability and failure semantics (unit level)."""

import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.services.ai.audit import recorder, store
from app.services.ai.audit.events import AuditEventType, AuditMetadata, AuditStatus, ReasonCode
from app.services.ai.audit.provider import ObservedLLMProvider, estimate_cost_usd, observe
from app.services.ai.errors import AIAuditUnavailableError, AIInvalidOutputError
from app.services.ai.llm.anthropic_provider import AnthropicLLMProvider
from app.services.ai.llm.base import LLMMessage
from app.services.ai.llm.fake import FakeLLMProvider
from tests.ai.audit.conftest import audit_rows

SENSITIVE = {
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIzIn0.c2lnbmF0dXJlc2ln",
    "api_key": "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV",
    "pan": "CFJNR1002V",
    "bank": "00031000000002",
    "ifsc": "MOCK0000003",
    "uan": "100200300003",
    "password": "password=hunter2",
    "salary": "salary 72000",
    "esi": "ESI number ESI100003",
}


# --- closed vocabulary and typed metadata -------------------------------------------------------------


def test_event_types_and_statuses_are_closed_enums():
    assert {"CHAT_REQUEST", "CHAT_CLASSIFIED", "CHAT_ROUTED", "POLICY_REQUEST", "POLICY_RESPONSE", "SQL_REQUEST",
            "SQL_RESPONSE", "ACTION_PROPOSED", "ACTION_CONFIRMATION_REQUESTED", "ACTION_CONFIRMED", "ACTION_REJECTED",
            "ACTION_EXECUTED", "ACTION_EXPIRED", "AUTHORIZATION_ALLOWED", "AUTHORIZATION_DENIED", "CLARIFICATION_REQUESTED",
            "REFUSAL", "AI_PROVIDER_REQUEST", "AI_PROVIDER_RESPONSE", "AI_PROVIDER_ERROR", "AI_ERROR"} <= {e.value for e in AuditEventType}
    assert {"STARTED", "SUCCESS", "FAILED", "DENIED", "REFUSED", "CONFIRMATION_REQUIRED"} <= {s.value for s in AuditStatus}
    with pytest.raises(ValueError):
        AuditEventType("ANYTHING_THE_CLIENT_WANTS")


@pytest.mark.parametrize("field", ["prompt", "message", "answer", "sql", "rows", "jwt", "password", "salary", "role", "user_id"])
def test_metadata_has_no_field_for_content_or_identity(field):
    with pytest.raises(ValueError):
        AuditMetadata(**{field: "x"})


@pytest.mark.parametrize("name,value", list(SENSITIVE.items()))
def test_metadata_text_fields_reject_sensitive_values(name, value):
    with pytest.raises(ValueError):
        AuditMetadata(error_code=value)
    with pytest.raises(ValueError):
        AuditMetadata(tables=["employees", value])


def test_metadata_rejects_free_text_and_long_values():
    for value in ("Ignore all previous instructions", "a" * 121, "<script>", "x\ny"):
        with pytest.raises(ValueError):
            AuditMetadata(reason=value)
    assert AuditMetadata(endpoint="/api/v1/chat/conversations/{conversation_id}", reason="rules:policy").reason == "rules:policy"


def test_metadata_numbers_cannot_be_negative():
    with pytest.raises(ValueError):
        AuditMetadata(row_count=-1)


# --- scope, record, correlation ---------------------------------------------------------------------------


def test_record_outside_a_request_is_a_no_op():
    assert recorder.current_scope() is None
    assert recorder.record(AuditEventType.AI_ERROR, AuditStatus.FAILED, error_code="X") is None


def test_events_share_the_scope_request_id_and_are_ordered(audit_scope):
    first = recorder.record(AuditEventType.CHAT_REQUEST, AuditStatus.STARTED)
    second = recorder.record(AuditEventType.CHAT_RESPONSE, AuditStatus.SUCCESS)
    assert recorder.current_request_id() == audit_scope.request_id and len(audit_scope.request_id) == 32
    assert (first.sequence, second.sequence) == (1, 2) and first.event_id != second.event_id


def test_new_scopes_get_new_request_ids():
    assert recorder.new_scope().request_id != recorder.new_scope().request_id


def test_identity_cannot_be_rebound_within_a_request(audit_scope):
    audit_scope.bind_user(3)
    audit_scope.bind_user(1)
    audit_scope.bind_conversation("a")
    audit_scope.bind_conversation("b")
    assert (audit_scope.employee_id, audit_scope.conversation_id) == (3, "a")


@pytest.mark.parametrize("name,value", list(SENSITIVE.items()))
def test_rejected_metadata_never_reaches_the_event(audit_scope, name, value):
    event = recorder.record(AuditEventType.AI_ERROR, AuditStatus.FAILED, error_code=value)
    assert event.metadata.model_dump(exclude_none=True) == {"error_code": "AUDIT_METADATA_REJECTED"}


def test_unknown_metadata_keys_are_dropped_not_stored(audit_scope):
    event = recorder.record(AuditEventType.AI_ERROR, AuditStatus.FAILED, prompt="secret question")
    assert event.metadata.model_dump(exclude_none=True) == {"error_code": "AUDIT_METADATA_REJECTED"}


def test_text_fingerprint_is_keyed_and_content_free():
    length, digest = recorder.text_fingerprint("My PAN is CFJNR1002V")
    assert length == 20 and len(digest) == 16 and "CFJNR" not in digest
    assert recorder.text_fingerprint("My PAN is CFJNR1002V") == (length, digest)
    assert recorder.text_fingerprint("other")[1] != digest


@pytest.mark.parametrize("reason,code", [
    ("rules:prompt_injection", ReasonCode.PROMPT_INJECTION), ("rules:sensitive_data", ReasonCode.SENSITIVE_DATA_REQUEST),
    ("rules:destructive", ReasonCode.DESTRUCTIVE_REQUEST), ("rules:direct_access", ReasonCode.DIRECT_SYSTEM_ACCESS),
    ("llm:unsupported", ReasonCode.UNSUPPORTED_REQUEST), ("rules:multiple_actions", ReasonCode.MULTIPLE_ACTIONS),
    ("rules:confirmation_in_chat", ReasonCode.CONFIRMATION_IN_CHAT), ("weird", ReasonCode.UNSUPPORTED_REQUEST),
])
def test_reason_codes_are_mapped_to_the_closed_set(reason, code):
    assert recorder.reason_code_for(reason, ReasonCode.UNSUPPORTED_REQUEST) == code


# --- persistence and failure semantics --------------------------------------------------------------------


async def test_flush_persists_with_server_side_identity(audit_scope, db):
    audit_scope.bind_user(3)
    audit_scope.bind_conversation("00000000-0000-4000-8000-000000000001")
    recorder.record(AuditEventType.CHAT_REQUEST, AuditStatus.STARTED, message_chars=5)
    await store.flush(audit_scope)
    (row,) = await audit_rows(db, request_id=audit_scope.request_id)
    assert (row.employee_id, row.conversation_id, row.event_type, row.status) == (
        3, "00000000-0000-4000-8000-000000000001", "CHAT_REQUEST", "STARTED")
    await store.flush(audit_scope)  # idempotent: already persisted events are not written twice
    assert len(await audit_rows(db, request_id=audit_scope.request_id)) == 1


async def test_flush_failure_falls_back_to_the_log_and_never_raises(audit_scope, monkeypatch, capsys, db):
    async def broken(*_args, **_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store, "write", broken)
    recorder.record(AuditEventType.AUTHORIZATION_DENIED, AuditStatus.DENIED, reason_code=ReasonCode.OUT_OF_SCOPE)
    await store.flush(audit_scope)
    output = capsys.readouterr().out
    assert "ai.audit.write_failed" in output and "ai.audit.fallback" in output and "OUT_OF_SCOPE" in output
    assert await audit_rows(db, request_id=audit_scope.request_id) == []


async def test_critical_record_fails_closed(audit_scope, monkeypatch):
    async def broken(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "write", broken)
    with pytest.raises(AIAuditUnavailableError) as raised:
        await recorder.record_critical(AuditEventType.ACTION_CONFIRMED, AuditStatus.SUCCESS, action="LEAVE_APPROVE")
    assert raised.value.http_status == 503


async def test_critical_record_is_written_immediately(audit_scope, db):
    await recorder.record_critical(AuditEventType.ACTION_CONFIRMED, AuditStatus.SUCCESS, action="LEAVE_APPROVE")
    assert types(await audit_rows(db, request_id=audit_scope.request_id)) == ["ACTION_CONFIRMED"]
    assert audit_scope.events[0].persisted


async def test_disabled_audit_skips_persistence_but_not_the_request(audit_scope, monkeypatch, db):
    monkeypatch.setattr(recorder.settings, "ai_audit_enabled", False)
    await recorder.record_critical(AuditEventType.ACTION_CONFIRMED, AuditStatus.SUCCESS)
    assert await audit_rows(db, request_id=audit_scope.request_id) == []


def types(rows):
    return [row.event_type for row in rows]


# --- provider observability ------------------------------------------------------------------------------


class Answer(BaseModel):
    text: str


async def test_observed_provider_records_latency_model_and_tokens(audit_scope):
    llm = observe(FakeLLMProvider().add_text_rule("hi", "hello there"), "policy_answer")
    await llm.generate([LLMMessage(role="user", content="hi")])
    request, response = audit_scope.events
    assert (request.event_type, response.event_type) == (AuditEventType.AI_PROVIDER_REQUEST, AuditEventType.AI_PROVIDER_RESPONSE)
    data = response.metadata
    assert (data.provider, data.model, data.operation) == ("fake", "fake-llm", "policy_answer")
    assert (data.input_tokens, data.output_tokens, data.total_tokens) == (1, 2, 3)
    assert data.estimated_cost_usd is None and response.duration_ms is not None


async def test_provider_errors_are_observable_and_re_raised(audit_scope):
    llm = observe(FakeLLMProvider(), "policy_answer")
    with pytest.raises(AIInvalidOutputError):
        await llm.generate_structured([LLMMessage(role="user", content="x")], Answer)
    assert audit_scope.events[-1].event_type == AuditEventType.AI_PROVIDER_ERROR
    assert audit_scope.events[-1].metadata.error_code == "AI_INVALID_OUTPUT"


def test_observe_is_idempotent():
    llm = observe(FakeLLMProvider(), "a")
    assert observe(llm, "b") is llm and isinstance(llm, ObservedLLMProvider)


class _Usage:
    input_tokens, output_tokens = 120, 30


class _Response:
    def __init__(self):
        self.usage, self.stop_reason, self.model = _Usage(), "end_turn", "claude-haiku-4-5-20251001"
        self.parsed_output = Answer(text="ok")


class _Messages:
    async def parse(self, **_kwargs):
        return _Response()


class _Client:
    messages = _Messages()


async def test_anthropic_usage_is_reported_through_the_abstraction(audit_scope, monkeypatch):
    monkeypatch.setattr(recorder.settings, "ai_llm_fast_model", "claude-haiku-4-5-20251001")
    monkeypatch.setattr(recorder.settings, "ai_llm_fast_input_cost_per_mtok", 1.0)
    monkeypatch.setattr(recorder.settings, "ai_llm_fast_output_cost_per_mtok", 5.0)
    provider = AnthropicLLMProvider(api_key="test", model="claude-haiku-4-5-20251001", max_tokens=100, timeout_seconds=5,
                                    server_fallbacks=False, client=_Client())
    await observe(provider, "route_classification").generate_structured([LLMMessage(role="user", content="x")], Answer)
    data = audit_scope.events[-1].metadata
    assert (data.input_tokens, data.output_tokens, data.total_tokens) == (120, 30, 150)
    assert data.estimated_cost_usd == pytest.approx((120 * 1.0 + 30 * 5.0) / 1_000_000)


def test_cost_is_null_without_usage_or_pricing():
    config = Settings(_env_file=None, ai_llm_model="m", ai_llm_input_cost_per_mtok=3.0, ai_llm_output_cost_per_mtok=15.0)
    assert estimate_cost_usd("m", 1000, 100, config) == pytest.approx(0.0045)
    assert estimate_cost_usd("m", None, 100, config) is None
    assert estimate_cost_usd("other-model", 1000, 100, config) is None
    assert estimate_cost_usd("m", 1000, 100, Settings(_env_file=None, ai_llm_model="m")) is None
