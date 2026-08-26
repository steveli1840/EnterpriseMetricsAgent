from pathlib import Path

import yaml

from app.domain.metrics import MetricDefinition


FALLBACK_METRICS = [
    MetricDefinition(
        name="delivered_revenue",
        version=1,
        label="已交付收入",
        description="Delivered order item price plus freight value.",
        model="analytics.fct_order_items",
        expression="price + freight_value",
        aggregation="sum",
        time_dimension="order_purchase_at",
        grain="order_item",
        allowed_dimensions=["customer_state", "product_category", "seller_state", "month"],
        filters=["order_status = 'delivered'"],
        owner="analytics",
        status="published",
    ),
    MetricDefinition(
        name="order_count",
        version=1,
        label="订单数",
        description="Distinct number of orders.",
        model="analytics.fct_order_items",
        expression="order_id",
        aggregation="count_distinct",
        time_dimension="order_purchase_at",
        grain="order",
        allowed_dimensions=["customer_state", "product_category", "seller_state", "month"],
        filters=[],
        owner="analytics",
        status="published",
    ),
    MetricDefinition(
        name="average_review_score",
        version=1,
        label="平均评论分",
        description="Average review score for reviewed orders.",
        model="analytics.fct_reviews",
        expression="review_score",
        aggregation="avg",
        time_dimension="review_created_at",
        grain="review",
        allowed_dimensions=["customer_state", "product_category", "month"],
        filters=[],
        owner="customer_experience",
        status="published",
    ),
]


def load_metrics(directory: Path | None = None) -> list[MetricDefinition]:
    root = directory or Path("metrics")
    if not root.exists():
        return FALLBACK_METRICS
    metrics = [
        MetricDefinition.model_validate(yaml.safe_load(path.read_text()))
        for path in sorted(root.glob("*.yaml"))
    ]
    return metrics or FALLBACK_METRICS


DEFAULT_METRICS = load_metrics()
