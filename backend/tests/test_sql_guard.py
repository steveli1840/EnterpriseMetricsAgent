import pytest

from app.domain.sql_guard import SQLPolicyError, validate_read_only_sql


def test_rejects_mutating_sql():
    with pytest.raises(SQLPolicyError, match="read-only"):
        validate_read_only_sql("DROP TABLE analytics.orders", {"analytics.orders"})


def test_rejects_unauthorized_table():
    with pytest.raises(SQLPolicyError, match="not authorized"):
        validate_read_only_sql("SELECT * FROM secret.payroll", {"analytics.orders"})


def test_accepts_cte_and_adds_limit():
    result = validate_read_only_sql(
        "WITH daily AS (SELECT order_id FROM analytics.orders) SELECT * FROM daily",
        {"analytics.orders"},
        max_rows=500,
    )
    assert result.sql.endswith("LIMIT 500")
    assert result.tables == ("analytics.orders",)


def test_accepts_clickhouse_system_catalog_select():
    result = validate_read_only_sql(
        "SELECT database, name FROM system.tables "
        "WHERE database IN ('analytics', 'raw_olist') ORDER BY database, name",
        {"system.tables", "system.columns"},
    )
    assert "system.tables" in result.tables
    assert result.sql.upper().endswith("LIMIT 1000")


def test_rejects_system_statement_but_not_system_schema():
    with pytest.raises(SQLPolicyError, match="read-only"):
        validate_read_only_sql("SYSTEM FLUSH LOGS", {"system.tables"})
