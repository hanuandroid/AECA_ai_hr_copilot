"""AIS-07 / AIS-08: everything a request writes to stdout/stderr, and what the AI tables keep. Synthetic data only.

The log check captures the process file descriptors (capfd), i.e. every logger and print, not only `ai.audit` lines,
across success and error paths: policy, SQL, a rejected SQL, a provider failure, an action proposal and its
confirmation through the real business endpoint.
"""

import json
import re

from sqlalchemy import select

from app.models.ai_conversation import AIConversationMessage
from app.models.ai_pending_action import AIPendingAction
from app.services.ai.actions.prompts import ACTION_SYSTEM_PROMPT
from app.services.ai.orchestration.models import RouterClassification
from app.services.ai.orchestration.prompts import ROUTER_SYSTEM_PROMPT
from app.services.ai.rag.models import PolicyLLMOutput
from app.services.ai.rag.prompts import SYSTEM_PROMPT as POLICY_SYSTEM_PROMPT
from app.services.ai.sql.models import SQLGenerationOutput
from app.services.ai.sql.prompts import SQL_SYSTEM_PROMPT
from tests.ai.orchestration.conftest import full_llm
from tests.support.data import EMPLOYEE

CHAT = "/api/v1/chat"
EMAIL, PHONE, PAN = "canary.person@example.test", "+91 98765 01234", "ZXCVB9876K"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJjYW5hcnkifQ.Y2FuYXJ5LXNpZ25hdHVyZQ"
CANARIES = (EMAIL, PHONE, "98765 01234", PAN, JWT)
SUFFIX = f" ({EMAIL}, {PHONE}, {PAN}, {JWT})"
PROMPT_TEXT = [prompt[:60] for prompt in (ROUTER_SYSTEM_PROMPT, POLICY_SYSTEM_PROMPT, SQL_SYSTEM_PROMPT, ACTION_SYSTEM_PROMPT)]


def _ticket_proposal(prompt: str) -> dict:
    references = " ".join(re.findall(r"\[PII_[A-Z]+_\d+\]", prompt))  # what a model would echo back
    return {"action": "TICKET_CREATE", "needs_clarification": False, "arguments": {
        "title": "Contact details update", "description": f"Please update my contact details: {references}",
        "category": "HR", "priority": "LOW"}}


async def test_no_request_writes_sensitive_content_to_stdout_or_stderr(client, auth, install_real, capfd):
    headers = await auth("employee")
    token = headers["Authorization"].split()[1]
    capfd.readouterr()  # discard setup output

    install_real(full_llm(router=lambda p: {"intent": "SQL", "confidence": 0.9, "reason": "test"},
                          sql="SELECT email FROM employees WHERE id = :me", action=_ticket_proposal))
    await client.post(CHAT, headers=headers, json={"message": "How many work from home WFH days per week does the "
                                                              "WFH policy allow?" + SUFFIX})
    sql_answer = await client.post(CHAT, headers=headers, json={"message": "What is my email address?" + SUFFIX})
    assert EMPLOYEE.email in sql_answer.text  # the user does get the value; the logs must not
    proposal = (await client.post(f"{CHAT}/actions", headers=headers,
                                  json={"question": "Create an HR ticket to update my contact details" + SUFFIX})).json()
    confirmation_id = proposal["data"]["confirmation_id"]
    confirmed = await client.post(f"{CHAT}/actions/confirm", headers=headers, json={"confirmation_id": confirmation_id})
    assert confirmed.json()["data"]["status"] == "completed"

    install_real(full_llm(sql="SELECT pan_number FROM employees WHERE id = :me"))  # rejected by the validator
    rejected = await client.post(f"{CHAT}/sql", headers=headers, json={"question": "Show my record" + SUFFIX})
    assert rejected.status_code >= 400
    from tests.ai.orchestration.conftest import MultiLLM
    install_real(MultiLLM({RouterClassification: {"intent": "POLICY", "confidence": 0.9, "reason": "t"},
                           SQLGenerationOutput: "not a dict"}))  # the model returns garbage: provider-output failure
    failed = await client.post(f"{CHAT}/sql", headers=headers, json={"question": "Count my tickets" + SUFFIX})
    assert failed.status_code >= 400
    install_real(MultiLLM({PolicyLLMOutput: {"answer": "x", "grounded": "maybe", "citations": []}}))
    await client.post(f"{CHAT}/policy", headers=headers, json={"question": "What is the WFH policy?" + SUFFIX})

    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert "ai.audit.event" in output  # the requests really logged something
    sql_texts = ("SELECT email FROM", "SELECT pan_number")  # model SQL (the audit's query_type=SELECT is a label)
    for forbidden in (*CANARIES, token, EMPLOYEE.email, confirmation_id, *sql_texts, *PROMPT_TEXT):
        assert forbidden not in output, forbidden


async def test_conversation_rows_never_keep_credentials_or_government_ids(client, auth, install_real, db):
    install_real(full_llm(router=lambda p: {"intent": "AMBIGUOUS", "confidence": 0.9, "reason": "t",
                                            "clarification_question": "What would you like to do?"}))
    headers = await auth("employee")
    await client.post(CHAT, headers=headers, json={"message": "Something else came up today" + SUFFIX})
    stored = "\n".join(m.content for m in (await db.execute(select(AIConversationMessage))).scalars())
    assert stored and JWT not in stored and PAN not in stored and "[REDACTED_TOKEN]" in stored
    # Pending decision G-09: contact details the user typed are still kept in their own (owner-only) history.
    assert EMAIL in stored and PHONE in stored


async def test_action_records_keep_business_values_but_no_credentials_or_confirmation_tokens(client, auth, install_real,
                                                                                            db):
    install_real(full_llm(action=_ticket_proposal))
    headers = await auth("employee")
    proposal = (await client.post(f"{CHAT}/actions", headers=headers,
                                  json={"question": "Create an HR ticket to update my contact details" + SUFFIX})).json()
    confirmation_id = proposal["data"]["confirmation_id"]
    await client.post(f"{CHAT}/actions/confirm", headers=headers, json={"confirmation_id": confirmation_id})

    record = (await db.execute(select(AIPendingAction))).scalars().one()
    row = json.dumps({column: str(getattr(record, column)) for column in record.__table__.columns.keys()})
    assert record.status == "COMPLETED" and record.used_at is not None
    assert confirmation_id not in row and JWT not in row  # only the token's hash; credentials never reach the model
    description = json.loads(record.payload_json)["body"]["description"]
    assert EMAIL in description and PHONE in description and PAN in description  # the user's own business values
