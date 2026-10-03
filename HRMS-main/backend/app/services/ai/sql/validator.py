"""Deterministic validation of LLM-generated SQL (pass 1: before any rewrite).

The LLM prompt is NOT a security boundary: every rule below is enforced on the AST.

Order: CTE names -> complexity -> functions -> bind parameters -> stars -> tables -> columns
(sqlglot qualify against the real schema) -> joins -> [non-ORG only] literal employee ids ->
[non-ORG only] scope intent.

Scope intent: for users without ORG scope, every scoped table in every SELECT must be restricted
to the caller (`owner = :me`), their reports (`employees.manager_id = :me`), or their team
(`owner IN (SELECT employee_id FROM my_team)` / JOIN my_team), directly or through an equality
join to such a table. A query that would mean "the whole organisation" (e.g. an EMPLOYEE's
`SELECT COUNT(*) FROM employees`) is therefore DENIED instead of silently answered with a
narrowed number. This check expresses intent only; the security guarantee is the scope rewrite
(scope.py) plus the read-only executor, which apply regardless of what the SQL says.
"""

from dataclasses import dataclass

from sqlglot import exp
from sqlglot.errors import OptimizeError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope

from app.services.ai.authorization import AIUserContext, Capability, DataScope, ScopeLevel, grant_for, sql_read_scope
from app.services.ai.authorization.sensitive import is_column_readable
from app.services.ai.errors import (
    AISQLError,
    SQLColumnNotAllowedError,
    SQLNotAllowedError,
    SQLQueryTooComplexError,
    SQLScopeViolationError,
    SQLTableNotAllowedError,
)
from app.services.ai.sql.parser import DIALECT
from app.services.ai.sql.schema import (
    DENIED_TABLES,
    EMPLOYEE_REFERENCE_COLUMNS,
    ME_PARAM,
    MY_TEAM,
    TABLES,
    TableKind,
    is_approved_join,
    sqlglot_schema,
)

MAX_SELECTS = 8
MAX_SUBQUERY_DEPTH = 3

ALLOWED_FUNCTION_NODES = frozenset(
    {"Count", "Sum", "Avg", "Min", "Max", "Abs", "Round", "Length", "Lower", "Upper", "Trim", "Substring",
     "Coalesce", "Nullif", "If", "Case", "Cast", "Date", "TimeToStr", "TsOrDsToTimestamp", "StrPosition",
     "Replace", "GroupConcat"}
)
ALLOWED_ANONYMOUS_FUNCTIONS = frozenset(
    {"DATETIME", "TIME", "JULIANDAY", "TOTAL", "IFNULL", "STRFTIME", "SUBSTR", "INSTR", "IIF", "LTRIM", "RTRIM", "DATE"}
)


@dataclass(frozen=True)
class ValidatedQuery:
    tree: exp.Expression  # qualified AST (all columns table-qualified)
    scope: DataScope
    tables: frozenset[str]
    uses_my_team: bool


def validate_generated_sql(tree: exp.Expression, ctx: AIUserContext, *, max_joins: int) -> ValidatedQuery:
    scope = sql_read_scope(ctx)
    if scope.level == ScopeLevel.NONE:
        raise SQLScopeViolationError(detail="no sql scope")

    cte_names = check_ctes(tree)
    check_complexity(tree, max_joins=max_joins, max_selects=MAX_SELECTS)
    check_functions(tree)
    check_placeholders(tree)
    check_stars(tree)
    tables, uses_my_team = _check_tables(tree, ctx, cte_names)
    qualified = _qualify(tree)
    _check_columns(qualified, ctx)
    _check_joins(qualified)
    if scope.level != ScopeLevel.ORG:
        _check_literal_employee_ids(qualified)
        _check_scope_intent(qualified)
    return ValidatedQuery(tree=qualified, scope=scope, tables=frozenset(tables), uses_my_team=uses_my_team)


# --- structural checks (reused by the final re-validation) ----------------------------------


def check_ctes(tree: exp.Expression) -> set[str]:
    names: set[str] = set()
    for with_node in tree.find_all(exp.With):
        if with_node.args.get("recursive"):
            raise SQLQueryTooComplexError(detail="recursive cte")
        for cte in with_node.expressions:
            name = cte.alias.lower()
            if name in TABLES or name in DENIED_TABLES or name == MY_TEAM or name.startswith("sqlite_"):
                raise SQLNotAllowedError(detail=f"cte shadows table {name}")
            names.add(name)
    return names


def check_complexity(tree: exp.Expression, *, max_joins: int, max_selects: int | None) -> None:
    selects = list(tree.find_all(exp.Select))
    if max_selects is not None and len(selects) > max_selects:
        raise SQLQueryTooComplexError(detail="too many selects")
    for select in selects:
        depth = sum(1 for ancestor in _ancestors(select) if isinstance(ancestor, exp.Select))
        if depth > MAX_SUBQUERY_DEPTH:
            raise SQLQueryTooComplexError(detail="subquery depth")
    joins = list(tree.find_all(exp.Join))
    if len(joins) > max_joins:
        raise SQLQueryTooComplexError(detail="too many joins")
    for join in joins:
        if join.args.get("using") or (join.args.get("kind") or "").upper() == "NATURAL":
            raise SQLNotAllowedError(detail="USING/NATURAL join")
        if join.args.get("on") is None:
            raise SQLQueryTooComplexError(detail="cartesian join")


def check_functions(tree: exp.Expression) -> None:
    for node in tree.find_all(exp.Func):
        if isinstance(node, exp.Connector):  # AND / OR are operators (sqlglot models them as Func)
            continue
        if isinstance(node, exp.Anonymous):
            if node.name.upper() not in ALLOWED_ANONYMOUS_FUNCTIONS:
                raise SQLNotAllowedError(detail=f"function {node.name}")
        elif type(node).__name__ not in ALLOWED_FUNCTION_NODES:
            raise SQLNotAllowedError(detail=f"function {type(node).__name__}")


def check_placeholders(tree: exp.Expression) -> None:
    for node in tree.find_all(exp.Placeholder):
        if node.name != ME_PARAM:
            raise SQLNotAllowedError(detail="unknown bind parameter")
    if any(True for _ in tree.find_all(exp.Parameter)):
        raise SQLNotAllowedError(detail="parameter syntax")


def check_stars(tree: exp.Expression) -> None:
    """`SELECT *` / `t.*` are always denied (explicit allowed columns are required); COUNT(*) is fine."""
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            raise SQLColumnNotAllowedError(detail="star projection")


# --- pass-1 only ----------------------------------------------------------------------------


def _check_tables(tree: exp.Expression, ctx: AIUserContext, cte_names: set[str]) -> tuple[set[str], bool]:
    tables: set[str] = set()
    uses_my_team = False
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if table.args.get("db") or table.args.get("catalog") or not name:
            raise SQLTableNotAllowedError(detail="qualified or anonymous table")
        if name in cte_names:
            continue
        if name == MY_TEAM:
            if grant_for(ctx.role, Capability.SQL_READ_TEAM) is None:
                raise SQLTableNotAllowedError(detail="my_team without team scope")
            uses_my_team = True
            continue
        policy = TABLES.get(name)
        if policy is None:
            raise SQLTableNotAllowedError(detail=f"table {name}")
        if policy.kind == TableKind.PAYROLL and not is_column_readable(ctx, name, "gross"):
            raise SQLTableNotAllowedError(detail="payroll requires PAYROLL_READ_ORG")
        tables.add(name)
    return tables, uses_my_team


def _qualify(tree: exp.Expression) -> exp.Expression:
    try:
        return qualify(tree.copy(), schema=sqlglot_schema(), dialect=DIALECT, validate_qualify_columns=True, expand_stars=False)
    except (OptimizeError, KeyError, ValueError) as exc:
        raise SQLColumnNotAllowedError(detail="unresolved column") from exc


def _check_columns(tree: exp.Expression, ctx: AIUserContext) -> None:
    for scope in traverse_scope(tree):
        for column in scope.columns:
            source = scope.sources.get(column.table)
            if not isinstance(source, exp.Table):
                continue  # derived table / CTE output: its own SELECT is validated in its scope
            table, name = source.name.lower(), column.name.lower()
            if table == MY_TEAM:
                if name != "employee_id":
                    raise SQLColumnNotAllowedError(detail="my_team column")
                continue
            policy = TABLES.get(table)
            if policy is None or name not in policy.allowed_columns or not is_column_readable(ctx, table, name):
                raise SQLColumnNotAllowedError(detail=f"column {table}.{name}")


def _check_joins(tree: exp.Expression) -> None:
    for select in tree.find_all(exp.Select):
        for join in select.args.get("joins") or []:
            joined = join.this
            if not isinstance(joined, exp.Table):
                continue  # joined derived table: its SELECT is validated independently
            joined_alias = joined.alias_or_name
            approved = False
            for conjunct in _conjuncts(join.args.get("on")):
                if isinstance(conjunct, exp.EQ) and isinstance(conjunct.left, exp.Column) and isinstance(conjunct.right, exp.Column):
                    left, right = _resolve(conjunct.left), _resolve(conjunct.right)
                    if left and right and joined_alias in (conjunct.left.table, conjunct.right.table) and is_approved_join(left, right):
                        approved = True
            if not approved:
                raise SQLNotAllowedError(detail=f"unapproved join to {joined.name}")


def _check_literal_employee_ids(tree: exp.Expression) -> None:
    """Non-ORG users may identify people only through :me / my_team, never by literal id."""
    comparisons = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Is)
    for node in tree.find_all(*comparisons, exp.In, exp.Between):
        operands = [node.this] if isinstance(node, (exp.In, exp.Between)) else [node.left, node.right]
        others = (
            list(node.expressions) if isinstance(node, exp.In)
            else [node.args.get("low"), node.args.get("high")] if isinstance(node, exp.Between)
            else [node.right, node.left]
        )
        for operand in operands:
            if isinstance(operand, exp.Column) and _resolve(operand) in EMPLOYEE_REFERENCE_COLUMNS:
                if any(other is not None and any(True for _ in other.find_all(exp.Literal)) for other in others):
                    raise SQLScopeViolationError(detail="literal employee id")


def _check_scope_intent(tree: exp.Expression) -> None:
    for select in tree.find_all(exp.Select):
        sources = _direct_tables(select)
        scoped = {alias for alias, name in sources.items() if name in TABLES and TABLES[name].kind != TableKind.CATALOG}
        if not scoped:
            continue
        constrained = {alias for alias, name in sources.items() if name == MY_TEAM}
        edges: list[tuple[str, str, str, str]] = []
        conjuncts = _conjuncts(select.args.get("where"))
        for join in select.args.get("joins") or []:
            conjuncts += _conjuncts(join.args.get("on"))

        for conjunct in conjuncts:
            if isinstance(conjunct, exp.EQ):
                column, other = _column_and_other(conjunct)
                if column is not None and isinstance(other, exp.Placeholder) and other.name == ME_PARAM:
                    table = sources.get(column.table)
                    if table and _is_me_constraint(table, column.name):
                        constrained.add(column.table)
                elif isinstance(conjunct.left, exp.Column) and isinstance(conjunct.right, exp.Column):
                    left, right = conjunct.left, conjunct.right
                    if left.table in sources and right.table in sources:
                        edges.append((left.table, left.name, right.table, right.name))
            elif isinstance(conjunct, exp.In) and isinstance(conjunct.this, exp.Column):
                table = sources.get(conjunct.this.table)
                query = conjunct.args.get("query")
                if table and conjunct.this.name in _owner_columns(table) and _selects_single_employee_key(query):
                    constrained.add(conjunct.this.table)

        changed = True
        while changed:
            changed = False
            for a1, c1, a2, c2 in edges + [(a2, c2, a1, c1) for a1, c1, a2, c2 in edges]:
                if a1 in constrained and a2 not in constrained and c1 in _key_columns(sources[a1]) and c2 in _owner_columns(sources[a2]):
                    constrained.add(a2)
                    changed = True

        unconstrained = scoped - constrained
        if unconstrained:
            raise SQLScopeViolationError(detail=f"unconstrained {sorted(sources[a] for a in unconstrained)}")


# --- helpers ----------------------------------------------------------------------------------


def _ancestors(node: exp.Expression):
    parent = node.parent
    while parent is not None:
        yield parent
        parent = parent.parent


def _conjuncts(condition: exp.Expression | None) -> list[exp.Expression]:
    if condition is None:
        return []
    if isinstance(condition, exp.Where):
        return _conjuncts(condition.this)
    if isinstance(condition, exp.And):
        return _conjuncts(condition.left) + _conjuncts(condition.right)
    if isinstance(condition, exp.Paren):
        return _conjuncts(condition.this)
    return [condition]


def _direct_tables(select: exp.Select) -> dict[str, str]:
    """alias -> table name for base tables in this SELECT's own FROM/JOINs (not nested selects)."""
    return {
        table.alias_or_name: table.name.lower()
        for table in select.find_all(exp.Table)
        if table.find_ancestor(exp.Select) is select
    }


def _resolve(column: exp.Column) -> tuple[str, str] | None:
    """(table, column) for a qualified column, looking through enclosing SELECTs (correlation)."""
    for ancestor in _ancestors(column):
        if isinstance(ancestor, exp.Select):
            table = _direct_tables(ancestor).get(column.table)
            if table is not None:
                return table, column.name.lower()
    return None


def _column_and_other(eq: exp.EQ) -> tuple[exp.Column | None, exp.Expression | None]:
    if isinstance(eq.left, exp.Column):
        return eq.left, eq.right
    if isinstance(eq.right, exp.Column):
        return eq.right, eq.left
    return None, None


def _owner_columns(table: str) -> tuple[str, ...]:
    if table == MY_TEAM:
        return ("employee_id",)
    return TABLES[table].owner_columns if table in TABLES else ()


def _key_columns(table: str) -> tuple[str, ...]:
    """Columns that identify the (already constrained) owner of a row."""
    return _owner_columns(table)


def _is_me_constraint(table: str, column: str) -> bool:
    return column in _owner_columns(table) or (table == "employees" and column == "manager_id")


def _selects_single_employee_key(query: exp.Expression | None) -> bool:
    select = query.this if isinstance(query, exp.Subquery) else query
    if not isinstance(select, exp.Select) or len(select.expressions) != 1:
        return False
    projected = select.expressions[0]
    projected = projected.this if isinstance(projected, exp.Alias) else projected
    if not isinstance(projected, exp.Column):
        return False
    resolved = _resolve(projected)
    # Only an owner key (employees.id, *.employee_id, my_team.employee_id) - e.g. selecting
    # manager_id would point at someone else's rows.
    return resolved is not None and resolved[1] in _owner_columns(resolved[0])
