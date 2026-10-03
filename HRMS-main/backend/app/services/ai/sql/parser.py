"""Parse untrusted SQL into exactly one read-only statement (sqlglot, SQLite dialect).

Security properties (all AST-based, never substring checks):
- exactly one statement (sqlglot splits on `;`; comments are dropped from the AST, and only SQL
  regenerated from the validated AST is ever executed, so text hidden in comments cannot run);
- the root must be a SELECT or a set operation of SELECTs (so e.g. `EXEC x`, which sqlglot parses
  as an alias expression, is rejected);
- no mutation / DDL / admin node anywhere in the tree, including inside CTEs and subqueries.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from app.services.ai.errors import SQLMutationRejectedError, SQLNotAllowedError, SQLParseError

DIALECT = "sqlite"
MAX_SQL_CHARS = 4000

# Data-changing statements (-> SQL_MUTATION_REJECTED). SQLite's `REPLACE INTO` is parsed as a
# Command and is mapped to a mutation below; the string function replace() (exp.Replace) is allowed.
_MUTATION_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Drop", "Create", "Alter", "AlterColumn", "TruncateTable",
)
# Other statements/constructs that are never allowed (-> SQL_NOT_ALLOWED).
_FORBIDDEN_NODE_NAMES = (
    "Command", "Pragma", "Attach", "Detach", "Grant", "Revoke", "Transaction", "Commit", "Rollback",
    "Set", "Use", "Copy", "LoadData", "Analyze", "Describe", "Show", "Into", "Returning", "Lock",
    "Execute", "Call",
)
_MUTATION_TYPES = tuple(t for t in (getattr(exp, n, None) for n in _MUTATION_NODE_NAMES) if t)
_FORBIDDEN_TYPES = tuple(t for t in (getattr(exp, n, None) for n in _FORBIDDEN_NODE_NAMES) if t)
_READ_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)


def parse_select(sql: str) -> exp.Expression:
    if not isinstance(sql, str) or not sql.strip():
        raise SQLParseError(detail="empty sql")
    if len(sql) > MAX_SQL_CHARS:
        raise SQLParseError(detail="sql too long")
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except (ParseError, TokenError) as exc:
        raise SQLParseError(detail="parse error") from exc

    if not statements:
        raise SQLParseError(detail="no statement")
    for statement in statements:  # reject mutations even when bundled with a SELECT
        _reject_forbidden_nodes(statement)
    if len(statements) != 1:
        raise SQLNotAllowedError(detail="multiple statements")

    root = statements[0]
    if not isinstance(root, _READ_ROOTS):
        raise SQLNotAllowedError(detail=f"root is {type(root).__name__}")
    return root


def _reject_forbidden_nodes(tree: exp.Expression) -> None:
    for node in tree.walk():
        if isinstance(node, _MUTATION_TYPES):
            raise SQLMutationRejectedError(detail=type(node).__name__)
        if isinstance(node, _FORBIDDEN_TYPES):
            if isinstance(node, exp.Command) and str(node.this).strip().upper() == "REPLACE":
                raise SQLMutationRejectedError(detail="REPLACE statement")
            raise SQLNotAllowedError(detail=type(node).__name__)
