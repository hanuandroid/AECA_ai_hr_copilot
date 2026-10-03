"""Phase 10 fixtures: the router/conversation fixtures plus the extra data the assignment prompts need."""

from typing import Any

import pytest

from app.models.employee_project import EmployeeProject
from app.models.employee_skill import EmployeeSkill
from app.models.enums import ProjectStatus, SkillLevel
from app.models.project import Project
from app.models.skill import Skill
from app.services.ai.rag.models import PolicyLLMOutput
from evals.policy_rag_eval import ExtractiveFakeLLM
from tests.ai.audit.conftest import audit_rows, meta, types  # noqa: F401
from tests.ai.conversation.conftest import (  # noqa: F401  (fixtures re-exported for this package)
    by_prompt,
    chat_service,
    make_conversations,
    script,
    sql,
)
from tests.ai.orchestration.conftest import (  # noqa: F401
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
from tests.support.data import EMPLOYEE, MANAGER, OUTSIDER

LEAVE_AUTOMATION = 2


@pytest.fixture(autouse=True)
async def assignment_data(db, router_data):
    """Skills and project assignments used by the assignment's suggested SQL prompts."""
    db.add_all([
        Project(id=LEAVE_AUTOMATION, name="Leave Automation Engine", description="Leave workflows", status=ProjectStatus.ONGOING),
        Skill(id=1, name="Python", normalized_name="python"),
        Skill(id=2, name="FastAPI", normalized_name="fastapi"),
    ])
    await db.flush()
    db.add_all([
        EmployeeProject(employee_id=MANAGER.id, project_id=1, role_on_project="Sponsor"),
        EmployeeProject(employee_id=EMPLOYEE.id, project_id=LEAVE_AUTOMATION, role_on_project="Backend Engineer"),
        EmployeeSkill(employee_id=EMPLOYEE.id, skill_id=1, level=SkillLevel.EXPERT),
        EmployeeSkill(employee_id=EMPLOYEE.id, skill_id=2, level=SkillLevel.INTERMEDIATE),
        EmployeeSkill(employee_id=OUTSIDER.id, skill_id=1, level=SkillLevel.BEGINNER),
    ])
    await db.commit()


class GroundedLLM(MultiLLM):
    """Scripted model for routing/SQL/actions; policy answers are extracted from the retrieved policy text."""

    def __init__(self, payloads: dict[type, Any] | None = None):
        super().__init__(payloads)
        self._policy = ExtractiveFakeLLM()

    async def generate_structured(self, messages, schema, *, system=None, max_tokens=None):
        if schema is PolicyLLMOutput:
            self.calls.append((schema.__name__, messages[-1].content))
            return await self._policy.generate_structured(messages, schema)
        return await super().generate_structured(messages, schema, system=system, max_tokens=max_tokens)
