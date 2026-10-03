"""Copilot router fixtures.

Two levels:
- spy services (`make_copilot`): the graph + classifier with recording stand-ins for the three
  specialised services, to test routing, state and loop guarantees in isolation;
- real services (`install_real` / `real_copilot`): the REAL Policy RAG, SQL agent and Action agent
  (real DB, retrieval, SQL validation/executor, confirmation store and business API) with only the
  LLM scripted, to prove the existing security boundaries still hold behind the router.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.main import app
from app.models.employee import Employee
from app.models.enums import EmployeeStatus, LeaveRequestStatus, LeaveType, Role
from app.models.leave_request import LeaveRequest
from app.services.ai.actions.client import HRMSActionClient
from app.services.ai.actions.models import ActionProposal, ActionResponse
from app.services.ai.actions.service import ActionAgentService, get_action_agent_service
from app.services.ai.authorization import AIUserContext, build_ai_user_context
from app.services.ai.embeddings import FakeEmbeddingProvider
from app.services.ai.errors import AIInvalidOutputError
from app.services.ai.orchestration.classifier import RouterClassifier, get_router_classifier
from app.services.ai.orchestration.models import RouterClassification
from app.services.ai.orchestration.service import CopilotService
from app.services.ai.rag.ingest import ingest_policies
from app.services.ai.rag.models import PolicyAnswer, PolicyLLMOutput
from app.services.ai.rag.service import PolicyRagService, get_policy_rag_service
from app.services.ai.rag.vector_store import SQLitePolicyVectorStore
from app.services.ai.sql.executor import ReadOnlySQLExecutor
from app.services.ai.sql.models import SQLAnswer, SQLFormatOutput, SQLGenerationOutput
from app.services.ai.sql.service import SQLAgentService, get_sql_agent_service
from tests.conftest import TEST_DB_PATH
from tests.support.data import MANAGER, USERS, future

TODAY = date(2026, 10, 1)
RAHUL, RAHUL_OUTSIDE, JOHN = 20, 21, 22  # Rahul Mehta + John Mathew report to MANAGER; Rahul Verma does not
RAHUL_LEAVE, RAHUL_OUTSIDE_LEAVE = 20, 21


@pytest.fixture(autouse=True)
async def router_data(db):
    hashed = (await db.execute(select(Employee.hashed_password).where(Employee.id == MANAGER.id))).scalar_one()
    for employee_id, name, manager_id in ((RAHUL, "Rahul Mehta", MANAGER.id), (RAHUL_OUTSIDE, "Rahul Verma", None),
                                          (JOHN, "John Mathew", MANAGER.id)):
        db.add(Employee(id=employee_id, name=name, email=f"{name.replace(' ', '.').lower()}@hrms-test.dev",
                        hashed_password=hashed, department_id=1, manager_id=manager_id, role=Role.EMPLOYEE,
                        status=EmployeeStatus.ACTIVE, joining_date=date(2023, 1, 1)))
    await db.flush()
    db.add_all([
        LeaveRequest(id=RAHUL_LEAVE, employee_id=RAHUL, leave_type=LeaveType.CASUAL, start_date=future(30),
                     end_date=future(30), reason="Family event", status=LeaveRequestStatus.PENDING),
        LeaveRequest(id=RAHUL_OUTSIDE_LEAVE, employee_id=RAHUL_OUTSIDE, leave_type=LeaveType.SICK, start_date=future(31),
                     end_date=future(31), reason="Clinic visit", status=LeaveRequestStatus.PENDING),
    ])
    await db.commit()


@pytest.fixture
def ctx_for(db) -> Callable:
    async def _ctx_for(user_key: str) -> AIUserContext:
        employee = (await db.execute(select(Employee).where(Employee.id == USERS[user_key].id))).scalar_one()
        return await build_ai_user_context(employee, db)

    return _ctx_for


# --- scripted LLMs --------------------------------------------------------------------------


class MultiLLM:
    """One untrusted fake model for every schema. A payload is a dict, or a callable(prompt) -> dict.
    Output is validated against the requested schema exactly like a real provider."""

    name = model = "scripted-multi"

    def __init__(self, payloads: dict[type, Any] | None = None):
        self.payloads: dict[type, Any] = dict(payloads or {})
        self.calls: list[tuple[str, str]] = []  # (schema name, last user message)

    async def generate(self, *args, **kwargs):
        raise NotImplementedError

    async def generate_structured(self, messages, schema, *, system=None, max_tokens=None):
        prompt = messages[-1].content
        self.calls.append((schema.__name__, prompt))
        payload = self.payloads.get(schema)
        if payload is None:
            raise AIInvalidOutputError()
        try:
            return schema.model_validate(payload(prompt) if callable(payload) else payload)
        except Exception as exc:
            raise AIInvalidOutputError() from exc

    def count(self, schema: type) -> int:
        return sum(1 for name, _ in self.calls if name == schema.__name__)


def classification(intent: str, confidence: float = 0.9, **extra: Any) -> dict[str, Any]:
    return {"intent": intent, "confidence": confidence, "reason": "test", **extra}


def router_llm(intent: str, confidence: float = 0.9, **extra: Any) -> MultiLLM:
    return MultiLLM({RouterClassification: classification(intent, confidence, **extra)})


def action_proposal(action: str, **fields: Any) -> dict[str, Any]:
    arguments = fields.pop("arguments", {})
    return {"action": action, "needs_clarification": False, **fields, "arguments": arguments}


# --- spy services (routing in isolation) -----------------------------------------------------


@dataclass
class SpyPolicy:
    calls: list[tuple[AIUserContext, str]] = field(default_factory=list)
    error: Exception | None = None

    async def answer(self, ctx, question):
        self.calls.append((ctx, question))
        if self.error:
            raise self.error
        return PolicyAnswer(answer="Sick leave: 10 days per year.", grounded=True, sources=[])


@dataclass
class SpySQL:
    calls: list[tuple[AIUserContext, str]] = field(default_factory=list)
    error: Exception | None = None

    async def answer(self, ctx, question):
        self.calls.append((ctx, question))
        if self.error:
            raise self.error
        return SQLAnswer(answer="You have 9 sick leaves remaining.", scope="SELF", columns=["remaining"],
                         rows=[{"remaining": 9}], row_count=1)


@dataclass
class SpyActions:
    calls: list[tuple[AIUserContext, str]] = field(default_factory=list)
    confirm_calls: list[Any] = field(default_factory=list)
    error: Exception | None = None
    status: str = "confirmation_required"

    async def propose(self, ctx, question):
        self.calls.append((ctx, question))
        if self.error:
            raise self.error
        if self.status == "confirmation_required":
            return ActionResponse(status="confirmation_required", message="Please confirm this action.",
                                  action="LEAVE_APPROVE", summary="Approve leave #20", confirmation_id="tok",
                                  expires_at="2026-10-01T09:05:00Z")
        return ActionResponse(status=self.status, message="Which leave request?", action="LEAVE_APPROVE")

    async def confirm(self, *args, **kwargs):  # present only so tests can prove the graph never calls it
        self.confirm_calls.append(args)
        raise AssertionError("the router must never confirm actions")


@dataclass
class Spies:
    policy: SpyPolicy = field(default_factory=SpyPolicy)
    sql: SpySQL = field(default_factory=SpySQL)
    actions: SpyActions = field(default_factory=SpyActions)

    @property
    def total_calls(self) -> int:
        return len(self.policy.calls) + len(self.sql.calls) + len(self.actions.calls) + len(self.actions.confirm_calls)


class CountingFactory:
    def __init__(self, provider):
        self.provider = provider
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.provider


@pytest.fixture
def spies() -> Spies:
    return Spies()


@pytest.fixture
def make_copilot(spies) -> Callable:
    """make_copilot(classifier_llm=None, **service_kwargs) -> (CopilotService, CountingFactory for the classifier LLM)."""

    def _make(classifier_llm=None, **kwargs):
        factory = CountingFactory(classifier_llm or MultiLLM())
        service = CopilotService(classifier=RouterClassifier(llm_factory=factory), policy=spies.policy, sql=spies.sql,
                                 actions=spies.actions, **kwargs)
        return service, factory

    return _make


# --- real services (fake LLM only) -----------------------------------------------------------


def _real_services(db: AsyncSession, llm: MultiLLM, app_for_client) -> tuple[PolicyRagService, SQLAgentService, ActionAgentService]:
    embedder = FakeEmbeddingProvider()
    policy = PolicyRagService(store_factory=lambda model_id: SQLitePolicyVectorStore(db, model_id),
                              embedder_factory=lambda: embedder, llm_factory=lambda: llm, top_k=4, min_score=0.35)
    sql = SQLAgentService(llm_factory=lambda: llm,
                          executor_factory=lambda: ReadOnlySQLExecutor(TEST_DB_PATH, timeout_seconds=3.0, max_rows=100),
                          max_rows=100, max_joins=4, today=lambda: TODAY)
    actions = ActionAgentService(db=db, llm_factory=lambda: llm,
                                 client_factory=lambda token: HRMSActionClient(app_for_client, token),
                                 ttl_seconds=settings.ai_action_confirmation_ttl_seconds, today=lambda: TODAY)
    return policy, sql, actions


@pytest.fixture
async def ingested(db):
    return await ingest_policies(db, FakeEmbeddingProvider(), upload_dir=settings.policy_upload_dir, chunk_size=800,
                                 chunk_overlap=120)


@pytest.fixture
def real_copilot(db, ingested) -> Callable:
    """real_copilot(llm) -> CopilotService over the REAL services (only the LLM is scripted)."""

    def _make(llm: MultiLLM) -> CopilotService:
        policy, sql, actions = _real_services(db, llm, app)
        return CopilotService(classifier=RouterClassifier(llm_factory=lambda: llm), policy=policy, sql=sql, actions=actions)

    return _make


@pytest.fixture
def install_real(ingested):
    """Route POST /chat (and the specialised endpoints) through the real services with a scripted LLM."""

    def _install(llm: MultiLLM) -> MultiLLM:
        embedder = FakeEmbeddingProvider()
        app.dependency_overrides[get_router_classifier] = lambda: RouterClassifier(llm_factory=lambda: llm)

        def _policy(db: AsyncSession = Depends(get_db)) -> PolicyRagService:
            return PolicyRagService(store_factory=lambda model_id: SQLitePolicyVectorStore(db, model_id),
                                    embedder_factory=lambda: embedder, llm_factory=lambda: llm, top_k=4, min_score=0.35)

        app.dependency_overrides[get_policy_rag_service] = _policy
        app.dependency_overrides[get_sql_agent_service] = lambda: SQLAgentService(
            llm_factory=lambda: llm,
            executor_factory=lambda: ReadOnlySQLExecutor(TEST_DB_PATH, timeout_seconds=3.0, max_rows=100),
            max_rows=100, max_joins=4, today=lambda: TODAY)

        def _actions(request: Request, db: AsyncSession = Depends(get_db)) -> ActionAgentService:
            return ActionAgentService(db=db, llm_factory=lambda: llm,
                                      client_factory=lambda token: HRMSActionClient(request.app, token),
                                      ttl_seconds=settings.ai_action_confirmation_ttl_seconds, today=lambda: TODAY)

        app.dependency_overrides[get_action_agent_service] = _actions
        return llm

    yield _install
    for dependency in (get_router_classifier, get_policy_rag_service, get_sql_agent_service, get_action_agent_service):
        app.dependency_overrides.pop(dependency, None)


def policy_output(answer: str = "Employees get 10 sick leave days per year.") -> dict[str, Any]:
    return {"answer": answer, "grounded": True, "citations": ["S1"]}


def full_llm(*, router: Any = None, sql: str | None = None, sql_answer: str = "Here are the results.",
             action: dict[str, Any] | None = None, policy: dict[str, Any] | None = None) -> MultiLLM:
    payloads: dict[type, Any] = {PolicyLLMOutput: policy or policy_output(), SQLFormatOutput: {"answer": sql_answer}}
    if router is not None:
        payloads[RouterClassification] = router
    if sql is not None:
        payloads[SQLGenerationOutput] = {"sql": sql, "explanation": "test"}
    if action is not None:
        payloads[ActionProposal] = action
    return MultiLLM(payloads)
