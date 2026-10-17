"""Conversation replay and policy audiences (RBAC remediation step 7, approved decision 3).

An assistant reply records the policies it cited (ai_conversation_messages.source_policy_ids, migration 0023). On
every replay (GET /chat/conversations/{id}) and when building follow-up context for the classifier, a reply is
replaced by a neutral notice if ANY cited policy is not readable by the caller now: its audience changed, the
caller's role changed, or the policy no longer exists. Replies without citations are untouched.
"""

import uuid

import pytest
from sqlalchemy import select, update

from app.models.ai_conversation import AIConversation
from app.models.employee import Employee
from app.models.enums import Role
from app.models.hr_policy import HRPolicy
from app.services.ai.conversation.service import ASSISTANT, USER, WITHHELD_REPLY
from tests.ai.conversation.conftest import make_conversations, script, stored_messages
from tests.support.data import USERS
from tests.support.http import data

SECRET_REPLY = "Directors receive the confidential retention bonus (codeword ZEPHYR-SEVEN)."


async def _seed(db, ctx, policy_ids: list[int] | None, reply: str = SECRET_REPLY) -> str:
    conversations = make_conversations(db)
    conversation = await conversations.create(ctx, "What is the retention bonus?")
    request_id = uuid.uuid4().hex
    await conversations.append_message(conversation, USER, "What is the retention bonus?", request_id=request_id, reserve=1)
    await conversations.append_message(conversation, ASSISTANT, reply, request_id=request_id, route="POLICY",
                                       status="answered", source_policy_ids=policy_ids)
    return conversation.public_id


async def _replay(client, auth, user: str, public_id: str) -> list[dict]:
    response = await client.get(f"/api/v1/chat/conversations/{public_id}", headers=await auth(user))
    assert response.status_code == 200, response.text
    return data(response)["messages"]


async def _first_policy(db) -> int:
    return (await db.execute(select(HRPolicy.id).where(HRPolicy.status == "PUBLISHED").order_by(HRPolicy.id))).scalars().first()


async def _set_audience(db, policy_id: int, audience: str) -> None:
    await db.execute(update(HRPolicy).where(HRPolicy.id == policy_id).values(audience=audience))
    await db.commit()


async def test_policy_replies_record_their_cited_policies(chat_service, ctx_for, db):
    response = await chat_service(script()).handle(await ctx_for("employee"), "What is the WFH policy?")
    cited = sorted({source["policy_id"] for source in response.parts[0].data["sources"]})
    assert cited
    messages = await stored_messages(db, response.conversation_id)
    assert messages[1].role == ASSISTANT and messages[1].source_policy_ids == cited
    assert messages[0].source_policy_ids is None  # user messages never carry citations


async def test_replay_withholds_a_reply_after_its_policy_is_restricted(chat_service, ctx_for, db, client, auth):
    response = await chat_service(script()).handle(await ctx_for("employee"), "What is the WFH policy?")
    before = await _replay(client, auth, "employee", response.conversation_id)
    assert before[1]["content"] == response.message
    for policy_id in {source["policy_id"] for source in response.parts[0].data["sources"]}:
        await _set_audience(db, policy_id, "ADMINS")
    after = await _replay(client, auth, "employee", response.conversation_id)
    assert after[0]["content"] == "What is the WFH policy?"  # the user's own text is unaffected
    assert after[1]["content"] == WITHHELD_REPLY and response.message not in str(after)


async def test_demoted_admin_no_longer_replays_admins_only_answers(db, ctx_for, client, auth):
    policy_id = await _first_policy(db)
    await _set_audience(db, policy_id, "ADMINS")
    public_id = await _seed(db, await ctx_for("admin"), [policy_id])
    assert (await _replay(client, auth, "admin", public_id))[1]["content"] == SECRET_REPLY
    # Another active admin keeps the system administrable; the original admin becomes a manager.
    await db.execute(update(Employee).where(Employee.id == USERS["outsider"].id).values(role=Role.ADMIN))
    await db.execute(update(Employee).where(Employee.id == USERS["admin"].id).values(role=Role.MANAGER))
    await db.commit()
    replay = await _replay(client, auth, "admin", public_id)
    assert replay[1]["content"] == WITHHELD_REPLY and "ZEPHYR" not in str(replay)


@pytest.mark.parametrize("user,audience,withheld", [
    ("employee", "ALL", False), ("employee", "MANAGERS", True), ("employee", "ADMINS", True),
    ("manager", "MANAGERS", False), ("manager", "ADMINS", True), ("admin", "ADMINS", False),
])
async def test_replay_matrix(db, ctx_for, client, auth, user, audience, withheld):
    policy_id = await _first_policy(db)
    await _set_audience(db, policy_id, audience)
    public_id = await _seed(db, await ctx_for(user), [policy_id])
    content = (await _replay(client, auth, user, public_id))[1]["content"]
    assert content == (WITHHELD_REPLY if withheld else SECRET_REPLY)


async def test_missing_cited_policy_fails_closed(db, ctx_for, client, auth):
    public_id = await _seed(db, await ctx_for("admin"), [999999])
    assert (await _replay(client, auth, "admin", public_id))[1]["content"] == WITHHELD_REPLY


async def test_one_hidden_citation_withholds_the_whole_reply(db, ctx_for, client, auth):
    ids = (await db.execute(select(HRPolicy.id).where(HRPolicy.status == "PUBLISHED").order_by(HRPolicy.id))).scalars().all()
    await _set_audience(db, ids[1], "MANAGERS")
    public_id = await _seed(db, await ctx_for("employee"), [ids[0], ids[1]])
    assert (await _replay(client, auth, "employee", public_id))[1]["content"] == WITHHELD_REPLY


async def test_replies_without_citations_are_untouched(db, ctx_for, client, auth):
    public_id = await _seed(db, await ctx_for("employee"), None, reply="You have 3 open tickets.")
    assert (await _replay(client, auth, "employee", public_id))[1]["content"] == "You have 3 open tickets."


async def test_followup_context_never_carries_a_withheld_reply(db, ctx_for):
    policy_id = await _first_policy(db)
    await _set_audience(db, policy_id, "ADMINS")
    ctx = await ctx_for("employee")
    public_id = await _seed(db, ctx, [policy_id])
    conversation = (await db.execute(select(AIConversation).where(AIConversation.public_id == public_id))).scalar_one()
    context = await make_conversations(db).get_recent_context(conversation, ctx)
    texts = [message.text for message in context.recent]
    assert WITHHELD_REPLY[:300] in texts and not any("ZEPHYR" in text for text in texts)
    assert context.anchor_question == "What is the retention bonus?"  # only the user's own text anchors a follow-up
