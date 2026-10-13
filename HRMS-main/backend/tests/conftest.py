"""Test harness.

Isolation model:
  1. At import time (before `app` is imported) point DATABASE_URL and upload dirs at a
     fresh temp directory, so the app engine never binds to storage/hrms.db.
  2. Once per session: run the real Alembic migrations on the temp DB, insert the
     deterministic test dataset, and snapshot the file as a template.
  3. Before every test: restore the template (fresh DB + policy files); after every test: dispose
     the engine so no connection outlives the test's event loop.
  4. At session end: delete the temp directory.
"""

import hashlib
import os
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(tempfile.mkdtemp(prefix="hrms_test_"))
TEST_DB_PATH = TEST_ROOT / "test.db"
TEMPLATE_DB_PATH = TEST_ROOT / "template.db"
POLICY_DIR = TEST_ROOT / "hr-policies"
TEMPLATE_POLICY_DIR = TEST_ROOT / "hr-policies-template"
REAL_DEV_DB_PATH = BACKEND_DIR / "storage" / "hrms.db"


def dev_db_fingerprint() -> str | None:
    """sha256 of the real dev DB file, or None if it does not exist (a fresh checkout has none)."""
    if not REAL_DEV_DB_PATH.exists():
        return None
    return hashlib.sha256(REAL_DEV_DB_PATH.read_bytes()).hexdigest()


# Taken before the app is imported, so test_infrastructure can prove the run never touched the dev DB.
DEV_DB_FINGERPRINT_AT_START = dev_db_fingerprint()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["POLICY_UPLOAD_DIR"] = str(POLICY_DIR)
os.environ["PROFILE_PHOTO_UPLOAD_DIR"] = str(TEST_ROOT / "profile-photos")
os.environ["EMPLOYEE_DOCUMENT_UPLOAD_DIR"] = str(TEST_ROOT / "employee-documents")
os.environ["JWT_SECRET_KEY"] = "test-only-secret-not-for-production"
os.environ["ALLOWED_HOSTS"] = "test,localhost,127.0.0.1"  # httpx test client host (TrustedHostMiddleware, B-25)
# The general suite exercises far more requests per minute than real users; dedicated tests (tests/ai/test_limits.py)
# set strict values via monkeypatch. Limiter state is reset before every test (fresh_db).
os.environ["AI_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["AUTH_LOGIN_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["AI_LLM_PROVIDER"] = "fake"
os.environ["AI_EMBEDDING_PROVIDER"] = "fake"
os.environ.pop("ANTHROPIC_API_KEY", None)

# App imports must come after the environment is configured.
import httpx  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from tests.support.data import INACTIVE, PASSWORD, USERS, seed_test_data  # noqa: E402

TOKEN_CACHE_SECONDS = 600  # well below the 15-minute access-token lifetime
_token_cache: dict[str, tuple[str, float]] = {}


def run_migrations() -> None:
    config = Config()  # no ini file: keeps alembic from reconfiguring pytest logging
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def template_db() -> Path:
    run_migrations()
    sync_engine = create_engine(f"sqlite:///{TEST_DB_PATH.as_posix()}")
    with Session(sync_engine) as session:
        seed_test_data(session)
    sync_engine.dispose()
    shutil.copyfile(TEST_DB_PATH, TEMPLATE_DB_PATH)
    shutil.copytree(POLICY_DIR, TEMPLATE_POLICY_DIR)
    return TEMPLATE_DB_PATH


async def reset_database(template: Path) -> None:
    """Restore both the DB and the policy files it references (tests may edit/delete files)."""
    await engine.dispose()
    shutil.copyfile(template, TEST_DB_PATH)
    shutil.rmtree(POLICY_DIR, ignore_errors=True)
    shutil.copytree(TEMPLATE_POLICY_DIR, POLICY_DIR)


@pytest.fixture(autouse=True)
async def fresh_db(template_db: Path) -> AsyncIterator[None]:
    from app.core.rate_limit import login_limiter
    from app.services.ai.limits import ai_request_limiter, llm_slots

    await reset_database(template_db)
    login_limiter.reset()
    ai_request_limiter.reset()
    llm_slots.reset()
    yield
    await engine.dispose()


def pytest_sessionfinish(session, exitstatus) -> None:
    shutil.rmtree(TEST_ROOT, ignore_errors=True)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.fixture
async def db() -> AsyncIterator:
    async with SessionLocal() as session:
        yield session


@pytest.fixture
def login(client: httpx.AsyncClient) -> Callable[[str], Awaitable[str]]:
    """Log in through the real POST /auth/login endpoint; tokens are cached per user.

    The INACTIVE user can no longer log in (production hardening B-03), so their token is minted exactly as
    /auth/login would have issued it: it models a token obtained before the account was deactivated, which every
    protected endpoint must now reject."""

    async def _login(user_key: str) -> str:
        # Tokens expire after 15 minutes (wall clock). A long run - or a host sleep/resume that moves the Docker VM
        # clock forward mid-run - must not hand out an expired token, so cached tokens are refreshed after 10 minutes.
        cached = _token_cache.get(user_key)
        if cached is not None and time.time() - cached[1] < TOKEN_CACHE_SECONDS:
            return cached[0]
        user = USERS[user_key]
        if user is INACTIVE:
            token = create_access_token(subject=str(user.id), role=user.role.value)
        else:
            response = await client.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
            assert response.status_code == 200, response.text
            token = response.json()["data"]["access_token"]
        _token_cache[user_key] = (token, time.time())
        return token

    return _login


@pytest.fixture
def auth(login: Callable[[str], Awaitable[str]]) -> Callable[[str], Awaitable[dict[str, str]]]:
    async def _auth(user_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {await login(user_key)}"}

    return _auth
