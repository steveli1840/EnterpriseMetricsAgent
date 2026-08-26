from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.domain.metrics import MetricDefinition
from app.infrastructure.control_plane import ControlPlaneRepository


def test_upsert_and_get_metric():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    repo = ControlPlaneRepository(factory)

    metric = MetricDefinition(
        name="gmv",
        version=1,
        label="GMV",
        description="Gross merchandise value",
        model="analytics.fct_order_items",
        expression="price",
        aggregation="sum",
        time_dimension="order_purchase_at",
        grain="order_item",
        allowed_dimensions=["customer_state"],
        filters=[],
        owner="analytics",
        status="published",
    )
    saved = repo.upsert_metric(metric)
    assert saved.name == "gmv"
    loaded = repo.get_metric("gmv")
    assert loaded is not None
    assert loaded.label == "GMV"

    updated = metric.model_copy(update={"label": "GMV 更新"})
    repo.upsert_metric(updated)
    assert repo.get_metric("gmv").label == "GMV 更新"
    assert repo.sync_metrics_from_definitions([metric]) == 1
