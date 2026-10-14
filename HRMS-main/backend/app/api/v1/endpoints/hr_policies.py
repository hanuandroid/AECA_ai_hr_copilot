import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.response import error_response, success_response
from app.db.session import get_db
from app.models.employee import Employee
from app.models.enums import PolicyStatus, Role
from app.models.hr_policy import HRPolicy
from app.models.hr_policy_event import HRPolicyEvent
from app.services.auth import get_current_user, require_roles

router = APIRouter()
logger = structlog.get_logger()
ALLOWED_POLICY_EXTENSIONS = {".txt", ".md", ".pdf"}

# Publication workflow (approved 2026-10-14): organisation-wide policies feed Copilot answers, so only an ADMIN can
# publish. A MANAGER's upload is a SUBMITTED draft until an ADMIN publishes or rejects it. Only PUBLISHED policies are
# listed to everyone, downloadable by everyone and indexed for Policy RAG; drafts are visible to ADMINs and their
# submitter only (anyone else gets the same 404 as an unknown id). Publishing a title archives its previous published
# version. Every transition is recorded in the append-only hr_policy_events table.
POLICY_NOT_FOUND = "Policy not found"


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _visibility_filter(user: Employee):
    if user.role == Role.ADMIN:
        return None
    published = HRPolicy.status == PolicyStatus.PUBLISHED.value
    if user.role == Role.MANAGER:
        return or_(published, HRPolicy.uploaded_by == user.id)
    return published


def _is_visible(policy: HRPolicy, user: Employee) -> bool:
    return (user.role == Role.ADMIN or policy.status == PolicyStatus.PUBLISHED.value
            or (user.role == Role.MANAGER and policy.uploaded_by == user.id))


def _policy_payload(row: HRPolicy) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "category": row.category,
        "content": row.content,  # Legacy seeded rows may still use this.
        "original_filename": row.original_filename,
        "mime_type": row.mime_type,
        "size_bytes": row.size_bytes,
        "uploaded_by": row.uploaded_by,
        "checksum": row.checksum,
        "status": row.status,
        "version": row.version,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _record(db: AsyncSession, policy: HRPolicy, action: str, from_status: str | None, actor: Employee) -> None:
    db.add(HRPolicyEvent(policy_id=policy.id, action=action, from_status=from_status, to_status=policy.status,
                         actor_employee_id=actor.id, version=policy.version, checksum=policy.checksum,
                         created_at=_utcnow()))
    logger.info("hr_policy.status_changed", policy_id=policy.id, action=action, from_status=from_status,
                to_status=policy.status, version=policy.version, actor_employee_id=actor.id)


def _same_title(title: str):
    return func.lower(func.trim(HRPolicy.title)) == title.strip().lower()


async def _publish(db: AsyncSession, policy: HRPolicy, actor: Employee, from_status: str | None) -> None:
    """Make `policy` the single PUBLISHED version of its title; archive the previous published version(s)."""
    previous = (await db.execute(
        select(HRPolicy).where(_same_title(policy.title), HRPolicy.status == PolicyStatus.PUBLISHED.value,
                               HRPolicy.id != policy.id)
    )).scalars().all()
    for old in previous:
        old.status = PolicyStatus.ARCHIVED.value
        _record(db, old, "ARCHIVED", PolicyStatus.PUBLISHED.value, actor)
    policy.status = PolicyStatus.PUBLISHED.value
    policy.reviewed_by = actor.id
    policy.reviewed_at = _utcnow()
    _record(db, policy, "PUBLISHED", from_status, actor)


async def _reviewable(db: AsyncSession, policy_id: int) -> HRPolicy:
    policy = (await db.execute(select(HRPolicy).where(HRPolicy.id == policy_id))).scalar_one_or_none()
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_response("POLICY_NOT_FOUND", POLICY_NOT_FOUND))
    if policy.status != PolicyStatus.SUBMITTED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=error_response("POLICY_NOT_SUBMITTED", "Only submitted policies can be reviewed"))
    return policy


@router.get("")
async def list_policies(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: PolicyStatus | None = Query(default=None, alias="status"),
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conditions = [condition for condition in (
        _visibility_filter(current_user),
        HRPolicy.status == status_filter.value if status_filter else None,
    ) if condition is not None]
    total = (await db.execute(select(func.count(HRPolicy.id)).where(*conditions))).scalar_one()
    rows = (await db.execute(
        select(HRPolicy).where(*conditions).order_by(HRPolicy.id.asc()).limit(limit).offset(offset)
    )).scalars().all()
    return success_response({"items": [_policy_payload(row) for row in rows],
                             "meta": {"total": total, "limit": limit, "offset": offset}})


@router.post("/upload")
async def upload_policy_document(
    title: str = Form(min_length=3, max_length=220),
    category: str = Form(min_length=2, max_length=60),
    file: UploadFile = File(...),
    current_user: Employee = Depends(require_roles(Role.ADMIN, Role.MANAGER)),
    db: AsyncSession = Depends(get_db),
):
    upload_dir = Path(settings.policy_upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File name is required")
    extension = Path(file.filename).suffix.lower()
    if extension not in ALLOWED_POLICY_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .txt, .md, and .pdf are supported")

    raw = await file.read()
    if len(raw) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File too large (max 2 MB)")

    if extension in {".txt", ".md"}:
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only UTF-8 text is supported for .txt/.md") from exc

    stored_filename = f"{uuid4().hex}{extension}"
    stored_path = upload_dir / stored_filename
    stored_path.write_bytes(raw)

    checksum = hashlib.sha256(raw).hexdigest()
    mime_type = file.content_type or {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".pdf": "application/pdf",
    }.get(extension, "application/octet-stream")

    latest_version = (await db.execute(select(func.max(HRPolicy.version)).where(_same_title(title)))).scalar_one()
    policy = HRPolicy(
        title=title.strip(),
        category=category.strip(),
        content=None,
        original_filename=file.filename,
        file_path=str(stored_path),
        mime_type=mime_type,
        size_bytes=len(raw),
        uploaded_by=current_user.id,
        checksum=checksum,
        status=PolicyStatus.SUBMITTED.value,
        version=(latest_version or 0) + 1,
    )
    db.add(policy)
    await db.flush()
    _record(db, policy, "SUBMITTED", None, current_user)
    if current_user.role == Role.ADMIN:  # admins publish directly; managers' uploads await review
        await _publish(db, policy, current_user, PolicyStatus.SUBMITTED.value)
    await db.commit()
    await db.refresh(policy)
    return success_response(_policy_payload(policy))


@router.post("/{policy_id}/publish")
async def publish_policy(
    policy_id: int,
    current_user: Employee = Depends(require_roles(Role.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    policy = await _reviewable(db, policy_id)
    await _publish(db, policy, current_user, PolicyStatus.SUBMITTED.value)
    await db.commit()
    await db.refresh(policy)
    # Re-indexing is an operator step (scripts/ingest_policies.py): business endpoints never call AI services.
    return success_response({**_policy_payload(policy), "index_refresh_required": True})


@router.post("/{policy_id}/reject")
async def reject_policy(
    policy_id: int,
    current_user: Employee = Depends(require_roles(Role.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    policy = await _reviewable(db, policy_id)
    policy.status = PolicyStatus.REJECTED.value
    policy.reviewed_by = current_user.id
    policy.reviewed_at = _utcnow()
    _record(db, policy, "REJECTED", PolicyStatus.SUBMITTED.value, current_user)
    await db.commit()
    await db.refresh(policy)
    return success_response(_policy_payload(policy))


@router.get("/{policy_id}/events")
async def policy_events(
    policy_id: int,
    _: Employee = Depends(require_roles(Role.ADMIN)),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(HRPolicyEvent).where(HRPolicyEvent.policy_id == policy_id).order_by(HRPolicyEvent.id.asc())
    )).scalars().all()
    return success_response([
        {"id": row.id, "action": row.action, "from_status": row.from_status, "to_status": row.to_status,
         "actor_employee_id": row.actor_employee_id, "version": row.version, "checksum": row.checksum,
         "created_at": row.created_at.isoformat()}
        for row in rows
    ])


@router.get("/{policy_id}/download")
async def download_policy_document(
    policy_id: int,
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    policy = (await db.execute(select(HRPolicy).where(HRPolicy.id == policy_id))).scalar_one_or_none()
    if policy is None or not _is_visible(policy, current_user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=POLICY_NOT_FOUND)
    if not policy.file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy file not available")

    base_dir = Path(settings.policy_upload_dir).resolve()
    requested = Path(policy.file_path).resolve()
    if base_dir not in requested.parents and requested != base_dir:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid file path")
    if not requested.exists() or not requested.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy file missing on server")

    return FileResponse(
        path=str(requested),
        filename=policy.original_filename or requested.name,
        media_type=policy.mime_type or "application/octet-stream",
    )
