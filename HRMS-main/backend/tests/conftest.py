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

import os
import shutil
import tempfile
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

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["POLICY_UPLOAD_DIR"] = str(POLICY_DIR)
os.environ["PROFILE_PHOTO_UPLOAD_DIR"] = str(TEST_ROOT / "profile-photos")
os.environ["EMPLOYEE_DOCUMENT_UPLOAD_DIR"] = str(TEST_ROOT / "employee-documents")
os.environ["JWT_SECRET_KEY"] = "test-only-secret-not-for-production"
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
from tests.support.data import PASSWORD, USERS, seed_test_data  # noqa: E402

_token_cache: dict[str, str] = {}


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
    await reset_database(template_db)
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
    """Log in through the real POST /auth/login endpoint; tokens are cached per user."""

    async def _login(user_key: str) -> str:
        if user_key not in _token_cache:
            user = USERS[user_key]
            response = await client.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
            assert response.status_code == 200, response.text
            _token_cache[user_key] = response.json()["data"]["access_token"]
        return _token_cache[user_key]

    return _login


@pytest.fixture
def auth(login: Callable[[str], Awaitable[str]]) -> Callable[[str], Awaitable[dict[str, str]]]:
    async def _auth(user_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {await login(user_key)}"}

    return _auth
