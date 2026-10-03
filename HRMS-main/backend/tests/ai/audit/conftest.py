"""Audit fixtures: reuse the router/conversation fixtures (real services, scripted LLM, Rahul/John data)."""

import json

import pytest
from sqlalchemy import select

from app.models.ai_audit_event import AIAuditEvent
from app.services.ai.audit import recorder
from tests.ai.conversation.conftest import by_prompt, script, sql  # noqa: F401
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
    real_copilot,
    router_data,
)


async def audit_rows(db, **filters) -> list[AIAuditEvent]:
    db.expire_all()
    query = select(AIAuditEvent).order_by(AIAuditEvent.id)
    for name, value in filters.items():
        query = query.where(getattr(AIAuditEvent, name) == value)
    return list((await db.execute(query)).scalars())


def types(rows) -> list[str]:
    return [row.event_type for row in rows]


def meta(row) -> dict:
    return json.loads(row.metadata_json)


@pytest.fixture
def audit_scope():
    """An active audit scope for service-level tests (what the middleware does per HTTP request)."""
    scope = recorder.new_scope()
    token = recorder.activate(scope)
    yield scope
    recorder.deactivate(token)
