"""Scope enforcement (the security boundary for rows) + final re-validation.

After pass-1 validation, EVERY base table reference is replaced by a backend-built subquery:

    <table> AS t   ->   (SELECT <allowed columns> FROM <table> WHERE <owner> IN (<server ids>)) AS t

- SELF: ids = {caller}; TEAM: ids = caller + direct reports (employees.manager_id); ORG: no WHERE.
- tickets: rows raised by an in-scope employee OR assigned to the caller.
- catalog/payroll(ORG only): column projection only.
The ids come from AIUserContext (DB), never from the SQL, prompt or LLM. `my_team` is injected as
a backend CTE. Then LIMIT is enforced, the final SQL is regenerated from the AST, re-parsed and
verified again (every base table must sit inside exactly the expected wrapper).
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from app.services.ai.authorization import AIUserContext, DataScope, ScopeLevel
from app.services.ai.errors import SQLNotAllowedError
from app.services.ai.sql.parser import DIALECT, parse_select
from app.services.ai.sql.schema import ME_PARAM, MY_TEAM, TABLES, TableKind
from app.services.ai.sql.validator import (
    ValidatedQuery,
    check_complexity,
    check_ctes,
    check_functions,
    check_placeholders,
    check_stars,
)


@dataclass(frozen=True)
class ExecutableQuery:
    sql: str  # regenerated from the validated + rewritten AST; the only SQL ever executed
    params: dict[str, int]
    scope: DataScope
    tables: frozenset[str]
    row_limit: int


def wrapper_sql(table: str, scope: DataScope, ctx: AIUserContext) -> str:
    policy = TABLES[table]
    columns = ", ".join(f'"{column}"' for column in policy.allowed_columns)
    sql = f'SELECT {columns} FROM "{table}"'
    predicate = _predicate(table, scope, ctx)
    if predicate:
        sql += f" WHERE {predicate}"
    return _canonical(sql)


def my_team_sql(ctx: AIUserContext) -> str:
    return _canonical(" UNION ALL ".join(f'SELECT {int(i)} AS "employee_id"' for i in sorted(ctx.team_ids)))


def enforce_scope(validated: ValidatedQuery, ctx: AIUserContext, *, max_rows: int, max_joins: int) -> ExecutableQuery:
    tree = validated.tree.copy()
    for table in list(tree.find_all(exp.Table)):
        name = table.name.lower()
        if name not in TABLES:
            continue  # my_team or an LLM CTE name (CTE names cannot shadow real tables)
        wrapper = sqlglot.parse_one(wrapper_sql(name, validated.scope, ctx), read=DIALECT)
        table.replace(exp.Subquery(this=wrapper, alias=exp.TableAlias(this=exp.to_identifier(table.alias_or_name))))

    if validated.uses_my_team:
        cte = exp.CTE(this=sqlglot.parse_one(my_team_sql(ctx), read=DIALECT), alias=exp.TableAlias(this=exp.to_identifier(MY_TEAM)))
        existing = tree.args.get("with_")
        if existing is not None:
            existing.set("expressions", [cte, *existing.expressions])
        else:
            tree.set("with_", exp.With(expressions=[cte]))

    apply_row_limit(tree, max_rows)
    final_sql = tree.sql(dialect=DIALECT, comments=False)
    verify_final_sql(final_sql, ctx, validated.scope, max_rows=max_rows, max_joins=max_joins)
    return ExecutableQuery(
        sql=final_sql, params={ME_PARAM: ctx.employee_id}, scope=validated.scope, tables=validated.tables, row_limit=max_rows
    )


def apply_row_limit(tree: exp.Expression, max_rows: int) -> None:
    """Outermost LIMIT = min(requested literal, max_rows); non-literal or missing -> max_rows."""
    offset = tree.args.get("offset")
    if offset is not None and not _is_int_literal(offset.expression):
        raise SQLNotAllowedError(detail="non-literal offset")
    limit = tree.args.get("limit")
    requested = limit.expression if isinstance(limit, exp.Limit) else None
    if _is_int_literal(requested) and int(requested.this) <= max_rows:
        return
    tree.limit(max_rows, copy=False)


def verify_final_sql(sql: str, ctx: AIUserContext, scope: DataScope, *, max_rows: int, max_joins: int) -> None:
    """Pass 2: re-parse what will actually run and prove every rewrite/limit is in place."""
    tree = parse_select(sql)
    llm_ctes = {name for name in _cte_names(tree) if name != MY_TEAM}
    _without_my_team = tree.copy()
    for cte in list(_without_my_team.find_all(exp.CTE)):
        if cte.alias.lower() == MY_TEAM:
            cte.pop()
    check_ctes(_without_my_team)
    check_complexity(tree, max_joins=max_joins, max_selects=None)
    check_functions(tree)
    check_placeholders(tree)
    check_stars(tree)

    for cte in tree.find_all(exp.CTE):
        if cte.alias.lower() == MY_TEAM and cte.this.sql(dialect=DIALECT, comments=False) != my_team_sql(ctx):
            raise SQLNotAllowedError(detail="my_team cte mismatch")

    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name in llm_ctes:
            continue
        if name == MY_TEAM:
            if MY_TEAM not in _cte_names(tree):
                raise SQLNotAllowedError(detail="my_team not injected")
            continue
        if name not in TABLES:
            raise SQLNotAllowedError(detail=f"unexpected table {name}")
        wrapper = table.find_ancestor(exp.Select)
        if (
            wrapper is None
            or not isinstance(wrapper.parent, exp.Subquery)
            or wrapper.sql(dialect=DIALECT, comments=False) != wrapper_sql(name, scope, ctx)
        ):
            raise SQLNotAllowedError(detail=f"table {name} not scope-wrapped")

    limit = tree.args.get("limit")
    if not isinstance(limit, exp.Limit) or not _is_int_literal(limit.expression) or int(limit.expression.this) > max_rows:
        raise SQLNotAllowedError(detail="row limit missing")


def _predicate(table: str, scope: DataScope, ctx: AIUserContext) -> str | None:
    policy = TABLES[table]
    if policy.kind == TableKind.CATALOG or scope.level == ScopeLevel.ORG:
        return None
    ids = ", ".join(str(int(i)) for i in sorted(scope.employee_ids or {ctx.employee_id}))
    if table == "tickets":
        return f'("employee_id" IN ({ids}) OR "assignee_id" = {int(ctx.employee_id)})'
    (owner,) = policy.owner_columns
    return f'"{owner}" IN ({ids})'


def _canonical(sql: str) -> str:
    return sqlglot.parse_one(sql, read=DIALECT).sql(dialect=DIALECT, comments=False)


def _cte_names(tree: exp.Expression) -> set[str]:
    return {cte.alias.lower() for cte in tree.find_all(exp.CTE)}


def _is_int_literal(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Literal) and not node.is_string and str(node.this).isdigit()
