"""Load + normalise policy documents from the existing hr_policies library (read-only)."""

import hashlib
import re
from io import BytesIO
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hr_policy import HRPolicy
from app.services.ai.rag.models import PolicyDocument

logger = structlog.get_logger()

TEXT_EXTENSIONS = {".md", ".txt"}
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def normalise_text(raw: str) -> str:
    text = raw.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    text = _HTML_COMMENT_RE.sub("", text)  # maintainer notes (e.g. DEMO/SEED markers) are not policy
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def _read_file(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return path.read_text(encoding="utf-8")
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(path.read_bytes()))
        if reader.is_encrypted:
            return None
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    return None


def _resolve_under(base_dir: Path, file_path: str) -> Path | None:
    """Same containment rule as hr_policies.py::download_policy_document."""
    base = base_dir.resolve()
    candidate = Path(file_path).resolve()
    if candidate != base and base not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


async def load_policy_documents(db: AsyncSession, upload_dir: str) -> tuple[list[PolicyDocument], list[int]]:
    """Return (documents, failed_policy_ids). Unreadable/empty/out-of-dir files are skipped, never fatal."""
    documents: list[PolicyDocument] = []
    failed: list[int] = []
    rows = (await db.execute(select(HRPolicy).order_by(HRPolicy.id.asc()))).scalars().all()
    base_dir = Path(upload_dir)

    for row in rows:
        raw: str | None = None
        try:
            if row.content and row.content.strip():
                raw = row.content
            elif row.file_path:
                path = _resolve_under(base_dir, row.file_path)
                raw = _read_file(path) if path else None
        except Exception as exc:  # corrupt PDF, bad encoding, IO error
            logger.warning("ai.rag.load_failed", policy_id=row.id, error_type=type(exc).__name__)
            raw = None

        text = normalise_text(raw) if raw else ""
        if not text:
            failed.append(row.id)
            continue
        documents.append(
            PolicyDocument(
                policy_id=row.id,
                title=row.title.strip(),
                category=row.category.strip(),
                source_filename=row.original_filename or (Path(row.file_path).name if row.file_path else f"policy-{row.id}"),
                text=text,
                checksum=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            )
        )
    return documents, failed
