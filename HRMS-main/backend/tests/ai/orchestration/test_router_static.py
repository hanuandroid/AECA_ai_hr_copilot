"""Static guarantees of the orchestration package: no DB, HTTP, shell, code execution or confirmation."""

import ast
from pathlib import Path

import pytest

from app.main import app
from app.services.ai.authorization import get_ai_user_context
from app.services.ai.orchestration import graph as graph_module
from app.services.ai.orchestration.service import get_copilot_service

PACKAGE = Path(graph_module.__file__).parent
SOURCES = sorted(PACKAGE.glob("*.py"))
BACKEND = PACKAGE.parents[3]

FORBIDDEN_IMPORT_ROOTS = {"sqlalchemy", "aiosqlite", "sqlite3", "httpx", "requests", "urllib", "socket", "subprocess", "os",
                          "shutil", "pickle", "importlib", "anthropic", "sqlglot", "langchain", "langchain_community",
                          "langchain_openai"}
FORBIDDEN_IMPORT_MODULES = ("app.db", "app.models", "app.api", "app.services.auth", "app.core.security",
                            "app.services.ai.actions.client", "app.services.ai.actions.confirmation",
                            "app.services.ai.sql.executor", "langgraph.checkpoint")
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "setattr", "globals", "locals"}
FORBIDDEN_METHODS = {"confirm", "execute", "commit", "flush", "add", "add_all", "delete", "merge", "post", "put", "patch",
                     "request", "send", "run", "system", "popen"}


def _trees():
    return [(path.name, ast.parse(path.read_text(encoding="utf-8"))) for path in SOURCES]


def test_package_files_are_the_expected_set():
    # Phase 7 added followup.py (DB-free follow-up context); the package still has no DB/HTTP access.
    assert {path.name for path in SOURCES} == {"__init__.py", "classifier.py", "followup.py", "graph.py", "models.py",
                                               "prompts.py", "service.py", "state.py"}


@pytest.mark.parametrize("name,tree", _trees())
def test_no_forbidden_imports(name, tree):
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            assert module.split(".")[0] not in FORBIDDEN_IMPORT_ROOTS, (name, module)
            assert not module.startswith(FORBIDDEN_IMPORT_MODULES), (name, module)


@pytest.mark.parametrize("name,tree", _trees())
def test_no_code_execution_db_writes_http_or_confirmation_calls(name, tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in FORBIDDEN_CALLS, (name, node.func.id, node.lineno)
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr not in FORBIDDEN_METHODS, (name, node.func.attr, node.lineno)


def test_langchain_core_is_used_only_for_the_config_type():
    imports = [node for _, tree in _trees() for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("langchain_core")]
    assert [(node.module, [alias.name for alias in node.names]) for node in imports] == [
        ("langchain_core.runnables", ["RunnableConfig"])]


def test_action_interface_exposes_only_propose():
    members = {name for name in vars(graph_module.ActionProposer) if not name.startswith("_")}
    assert members == {"propose"}


def test_graph_node_names_are_literals():
    tree = ast.parse(Path(graph_module.__file__).read_text(encoding="utf-8"))
    builder = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "build_copilot_graph")
    nodes_dict = next(node for node in ast.walk(builder) if isinstance(node, ast.Dict))
    assert all(isinstance(key, ast.Constant) and isinstance(key.value, str) for key in nodes_dict.keys)


def test_unified_endpoint_uses_trusted_context_and_the_copilot_service():
    route = next(route for route in app.routes if getattr(route, "path", None) == "/api/v1/chat")
    assert route.methods == {"POST"}
    direct = {dependency.call for dependency in route.dependant.dependencies}
    assert get_ai_user_context in direct
    # Phase 7: the copilot service is reached through the conversation layer (get_conversational_copilot).
    pending, calls = list(route.dependant.dependencies), set()
    while pending:
        dependency = pending.pop()
        calls.add(dependency.call)
        pending.extend(dependency.dependencies)
    assert get_copilot_service in calls


def test_langgraph_is_pinned_and_langchain_is_not_a_direct_dependency():
    requirements = (BACKEND / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert "langgraph==1.2.12" in requirements
    assert not any(line.startswith(("langchain", "langchain-")) for line in requirements)


def test_no_migration_was_added_for_orchestration():
    # The router itself persists nothing. The only migration after Phase 5 is Phase 7's conversation store,
    # which is owned by services/ai/conversation (the orchestration package imports no models; see above).
    versions = sorted(path.name for path in (BACKEND / "alembic" / "versions").glob("*.py"))
    # Phase 8 adds the audit store (0020), owned by services/ai/audit; still none for the router itself.
    assert [name for name in versions if name > "0018_"] == ["0018_ai_pending_actions.py", "0019_ai_conversations.py",
                                                             "0020_ai_audit_events.py"]


def test_no_api_key_in_orchestration_sources():
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        assert "sk-ant" not in text and "ANTHROPIC_API_KEY=" not in text
