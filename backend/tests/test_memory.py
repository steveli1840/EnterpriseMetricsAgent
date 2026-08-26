from app.agent.memory import MemoryManager
from app.agent.service import ResolvedIntent
from app.domain.metrics import MetricDefinition


class FakeRepository:
    def __init__(self):
        self.snapshot = {"events": [], "focus": {}}

    def get_conversation_snapshot(self, user_id: str, conversation_id: str) -> dict:
        return {
            "events": list(self.snapshot.get("events", [])),
            "focus": dict(self.snapshot.get("focus") or {}),
        }

    def append_conversation_event(self, user_id: str, conversation_id: str, event: dict):
        self.snapshot.setdefault("events", []).append(event)
        return self.snapshot

    def update_conversation_focus(self, user_id: str, conversation_id: str, focus: dict):
        merged = dict(self.snapshot.get("focus") or {})
        merged.update(focus)
        self.snapshot["focus"] = merged
        return self.snapshot

    def list_user_memories(self, user_id: str, confirmed_only: bool = False):
        return []


def test_memory_manager_extracts_table_entity_from_catalog_rows():
    manager = MemoryManager()
    entities = manager.extract_entities(
        intent=ResolvedIntent(route="explore"),
        columns=["database", "name", "total_rows"],
        rows=[["raw_olist", "geolocation", 1000163]],
        sql="SELECT database, name, total_rows FROM system.tables",
        prior={},
    )
    assert entities["table"]["ref"] == "raw_olist.geolocation"
    assert entities["table"]["name"] == "geolocation"


def test_memory_manager_commits_working_memory_for_next_turn():
    repo = FakeRepository()
    manager = MemoryManager(repo)
    manager.commit_turn(
        user_id="analyst-1",
        conversation_id="c1",
        question="哪个表最大",
        answer="geolocation 最大",
        intent=ResolvedIntent(route="explore"),
        columns=["database", "name", "total_rows"],
        rows=[["raw_olist", "geolocation", 1000163]],
        sql="SELECT database, name, total_rows FROM system.tables",
        trace_id="t1",
    )
    memory = manager.load(user_id="analyst-1", conversation_id="c1")
    assert memory.working.entities["table"]["ref"] == "raw_olist.geolocation"
    assert memory.working.last_intent == "catalog.explore"
    assert memory.recent_turns[-1]["role"] == "assistant"
    prompt = memory.to_prompt_dict()
    assert prompt["working"]["entities"]["table"]["name"] == "geolocation"


def test_memory_manager_keeps_metric_entity():
    metric = MetricDefinition(
        name="delivered_revenue",
        version=1,
        label="已交付收入",
        description="Revenue from delivered orders",
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
    entities = MemoryManager.extract_entities(
        intent=ResolvedIntent(
            route="metric",
            is_metric_query=True,
            metric=metric,
            dimensions=["customer_state"],
        ),
        columns=["customer_state", "delivered_revenue"],
        rows=[["SP", 1.0]],
        sql="SELECT customer_state, sum(...) FROM analytics.fct_order_items",
        prior={},
    )
    assert entities["metric"]["name"] == "delivered_revenue"
    assert entities["table"]["ref"] == "analytics.fct_order_items"
