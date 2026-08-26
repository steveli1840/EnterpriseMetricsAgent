import re
from dataclasses import dataclass

try:
    import sqlglot
    from sqlglot import exp
except ImportError:  # pragma: no cover - only used by minimal local test environments
    sqlglot = None
    exp = None


class SQLPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedSQL:
    sql: str
    tables: tuple[str, ...]


# Match mutating verbs only as statement leaders.
# Do not use a bare \bSYSTEM\b — that falsely rejects ClickHouse `system.tables`.
_MUTATING = re.compile(
    r"(?:^|;)\s*(ALTER|ATTACH|CREATE|DELETE|DETACH|DROP|GRANT|INSERT|KILL|"
    r"OPTIMIZE|RENAME|REPLACE|REVOKE|SET|SYSTEM|TRUNCATE|UPDATE)\b",
    re.IGNORECASE | re.MULTILINE,
)


def _fallback_tables(statement: str) -> set[str]:
    cte_names = set(re.findall(r"(?:WITH|,)\s*([A-Za-z_][\w]*)\s+AS\s*\(", statement, re.I))
    raw = re.findall(r"\b(?:FROM|JOIN)\s+([`\w.-]+)", statement, re.I)
    return {name.strip("`") for name in raw if name.strip("`") not in cte_names}


def validate_read_only_sql(
    statement: str, allowed_tables: set[str], *, max_rows: int = 1000
) -> ValidatedSQL:
    sql = statement.strip().rstrip(";").strip()
    if not sql or ";" in sql or _MUTATING.search(sql):
        raise SQLPolicyError("only a single read-only query is allowed")

    if sqlglot:
        try:
            parsed = sqlglot.parse_one(sql, read="clickhouse")
        except sqlglot.errors.ParseError as exc:
            raise SQLPolicyError(f"SQL cannot be parsed: {exc}") from exc
        if not isinstance(parsed, (exp.Select, exp.Union)):
            raise SQLPolicyError("only a single read-only query is allowed")
        cte_names = {cte.alias_or_name for cte in parsed.find_all(exp.CTE)}
        tables = {
            table.sql(dialect="clickhouse")
            for table in parsed.find_all(exp.Table)
            if table.name not in cte_names
        }
        has_limit = parsed.args.get("limit") is not None
    else:
        if not re.match(r"^(SELECT|WITH)\b", sql, re.I):
            raise SQLPolicyError("only a single read-only query is allowed")
        tables = _fallback_tables(sql)
        has_limit = bool(re.search(r"\bLIMIT\s+\d+\s*$", sql, re.I))

    unauthorized = sorted(tables - allowed_tables)
    if unauthorized:
        raise SQLPolicyError(f"table is not authorized: {', '.join(unauthorized)}")
    if not has_limit:
        sql = f"{sql}\nLIMIT {max_rows}"
    return ValidatedSQL(sql=sql, tables=tuple(sorted(tables)))

