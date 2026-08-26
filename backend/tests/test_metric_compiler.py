from app.domain.metrics import MetricDefinition, compile_metric_query


def test_compiles_published_metric_with_dimension_and_time_filter():
    metric = MetricDefinition(
        name="delivered_revenue",
        version=1,
        label="Delivered revenue",
        description="Revenue for delivered orders",
        model="analytics.fct_order_items",
        expression="price + freight_value",
        aggregation="sum",
        time_dimension="order_purchase_at",
        grain="order_item",
        allowed_dimensions=["customer_state"],
        filters=["order_status = 'delivered'"],
        owner="analytics",
        status="published",
    )
    query = compile_metric_query(
        metric,
        dimensions=["customer_state"],
        start="2018-01-01",
        end="2018-02-01",
        dialect="clickhouse",
    )
    assert "sum(price + freight_value) AS delivered_revenue" in query
    assert "customer_state" in query
    assert "order_status = 'delivered'" in query
    assert "order_purchase_at >= {start:DateTime}" in query
    assert "GROUP BY customer_state" in query

