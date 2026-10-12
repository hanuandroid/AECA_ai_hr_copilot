"""Server-side confirmations (D6): single-use, user-, action-, target- and parameter-bound, time-limited.

- The client receives a random token once; only its SHA-256 is stored.
- binding_hash = SHA-256 over (employee_id, action, target, resource, exact payload). It is
  re-computed at confirmation time; any change to the stored record is detected.
- The confirm request carries ONLY the token: the action is recovered from the server record.
- Claiming is an atomic UPDATE ... WHERE status='PENDING' (replay-safe).
- Every failure mode (unknown, other user's, used, expired, tampered) yields the same error.
- This is the only module in the action agent that writes to the database, and it writes only the
  AI-owned ai_pending_actions table.
"""

import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_pending_action import AIPendingAction
from app.services.ai.actions.models import ActionName, PreparedAction
from app.services.ai.authorization import AIUserContext
from app.services.ai.errors import AIConfirmationInvalidError

TOKEN_MIN_CHARS, TOKEN_MAX_CHARS = 20, 200


def _canonical(prepared: PreparedAction) -> str:
    return json.dumps({"path_params": prepared.path_params, "body": prepared.body}, sort_keys=True, separators=(",", ":"))


def binding_hash(employee_id: int, action: str, target: int | None, resource: int | None, payload_json: str) -> str:
    material = json.dumps(
        {"employee_id": employee_id, "action": action, "target": target, "resource": resource, "payload": payload_json},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ConfirmationService:
    def __init__(self, db: AsyncSession, *, ttl_seconds: int, now: Callable[[], datetime]):
        self._db = db
        self._ttl = timedelta(seconds=ttl_seconds)
        self._now = now

    async def create(self, ctx: AIUserContext, prepared: PreparedAction) -> tuple[str, AIPendingAction]:
        token = secrets.token_urlsafe(32)
        payload = _canonical(prepared)
        created = self._now()
        record = AIPendingAction(
            token_hash=_hash_token(token),
            employee_id=ctx.employee_id,
            role=ctx.role.value,
            action=prepared.action.value,
            target_employee_id=prepared.target_employee_id,
            resource_id=prepared.resource_id,
            payload_json=payload,
            binding_hash=binding_hash(ctx.employee_id, prepared.action.value, prepared.target_employee_id, prepared.resource_id, payload),
            summary=prepared.summary,
            status="PENDING",
            created_at=created,
            expires_at=created + self._ttl,
        )
        self._db.add(record)
        await self._db.commit()
        return token, record

    async def load_valid(self, ctx: AIUserContext, token: object) -> tuple[AIPendingAction, PreparedAction]:
        if not isinstance(token, str) or not (TOKEN_MIN_CHARS <= len(token) <= TOKEN_MAX_CHARS):
            raise AIConfirmationInvalidError(detail="malformed token")
        record = (await self._db.execute(select(AIPendingAction).where(AIPendingAction.token_hash == _hash_token(token)))).scalar_one_or_none()
        if record is None:
            raise AIConfirmationInvalidError(detail="unknown token")
        if record.employee_id != ctx.employee_id:
            raise AIConfirmationInvalidError(detail="token belongs to another user")
        if record.status != "PENDING":
            raise AIConfirmationInvalidError(detail=f"status {record.status}")
        if record.expires_at <= self._now():
            await self._set_status(record.id, "EXPIRED", "AI_CONFIRMATION_EXPIRED")
            raise AIConfirmationInvalidError(detail="expired")
        expected = binding_hash(record.employee_id, record.action, record.target_employee_id, record.resource_id, record.payload_json)
        if expected != record.binding_hash:
            await self._set_status(record.id, "INVALID", "AI_CONFIRMATION_TAMPERED")
            raise AIConfirmationInvalidError(detail="binding mismatch")
        payload = json.loads(record.payload_json)
        prepared = PreparedAction(
            action=ActionName(record.action), target_employee_id=record.target_employee_id, resource_id=record.resource_id,
            path_params=payload["path_params"], body=payload["body"], summary=record.summary,
        )
        return record, prepared

    async def claim(self, record_id: int) -> None:
        """Atomically consume the confirmation; a concurrent or repeated confirm loses the race."""
        result = await self._db.execute(
            update(AIPendingAction)
            .where(AIPendingAction.id == record_id, AIPendingAction.status == "PENDING")
            .values(status="EXECUTING", used_at=self._now())
        )
        await self._db.commit()  # also releases the session's SQLite lock before the API call
        if result.rowcount != 1:
            raise AIConfirmationInvalidError(detail="already claimed")

    async def release(self, record_id: int) -> None:
        """Undo a claim when nothing was executed (the confirmation audit could not be written). Atomic: only an
        EXECUTING record returns to PENDING; expiry is still enforced on the next attempt."""
        await self._db.execute(
            update(AIPendingAction)
            .where(AIPendingAction.id == record_id, AIPendingAction.status == "EXECUTING")
            .values(status="PENDING", used_at=None)
        )
        await self._db.commit()

    async def finish(self, record_id: int, status: str, result_code: str | None) -> None:
        await self._set_status(record_id, status, result_code)

    async def _set_status(self, record_id: int, status: str, result_code: str | None) -> None:
        await self._db.execute(
            update(AIPendingAction).where(AIPendingAction.id == record_id).values(status=status, result_code=result_code)
        )
        await self._db.commit()
