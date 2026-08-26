from fastapi.testclient import TestClient

from app.main import create_app


def auth(client: TestClient, username: str, password: str) -> dict[str, str]:
    token = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_conversations_and_memories_are_scoped_to_authenticated_user():
    client = TestClient(create_app(testing=True))
    analyst = auth(client, "analyst", "analyst-demo")
    admin = auth(client, "admin", "admin-demo")

    created = client.post("/api/v1/conversations", json={"title": "Revenue"}, headers=analyst)
    assert created.status_code == 201
    assert len(client.get("/api/v1/conversations", headers=analyst).json()["items"]) == 1
    assert client.get("/api/v1/conversations", headers=admin).json()["items"] == []

    memory = client.post(
        "/api/v1/memories",
        json={"kind": "semantic", "value": {"term": "收入", "metric": "delivered_revenue"}},
        headers=analyst,
    )
    assert memory.status_code == 201
    assert memory.json()["status"] == "pending"
    confirmed = client.post(
        f"/api/v1/memories/{memory.json()['id']}/confirm", headers=analyst
    )
    assert confirmed.json()["status"] == "confirmed"


def test_governance_mutations_require_admin_role():
    client = TestClient(create_app(testing=True))
    analyst = auth(client, "analyst", "analyst-demo")
    admin = auth(client, "admin", "admin-demo")
    assert client.post("/api/v1/admin/metrics/sync", headers=analyst).status_code == 403
    assert client.post("/api/v1/admin/metrics/sync", headers=admin).status_code == 202


def test_data_source_configuration_requires_admin_and_masks_secrets():
    client = TestClient(create_app(testing=True))
    analyst = auth(client, "analyst", "analyst-demo")
    admin = auth(client, "admin", "admin-demo")

    listed = client.get("/api/v1/data-sources", headers=analyst)
    assert listed.status_code == 200
    assert listed.json()["items"][0]["is_active"] is True

    payload = {
        "name": "Warehouse B",
        "provider": "clickhouse",
        "is_active": True,
        "config": {
            "host": "warehouse-b",
            "port": 8123,
            "database": "analytics",
            "user": "readonly",
            "password": "secret",
        },
    }
    assert client.post("/api/v1/admin/data-sources", json=payload, headers=analyst).status_code == 403
    created = client.post("/api/v1/admin/data-sources", json=payload, headers=admin)
    assert created.status_code == 201
    assert created.json()["config"]["password"] == "***"
    assert created.json()["is_active"] is True


def test_admin_can_test_active_data_source_and_refresh_schema_snapshot():
    client = TestClient(create_app(testing=True))
    analyst = auth(client, "analyst", "analyst-demo")
    admin = auth(client, "admin", "admin-demo")

    assert client.post("/api/v1/admin/data-sources/test", headers=analyst).status_code == 403

    tested = client.post("/api/v1/admin/data-sources/test", headers=admin)
    assert tested.status_code == 200
    assert tested.json()["status"] == "ok"
    assert tested.json()["provider"] == "clickhouse"

    refreshed = client.post("/api/v1/admin/schemas/refresh", headers=admin)
    assert refreshed.status_code == 202
    assert refreshed.json()["status"] == "completed"
    assert refreshed.json()["columns"] == 3

    schemas = client.get("/api/v1/schemas", headers=admin).json()
    assert schemas["snapshot"] == refreshed.json()["snapshot"]
    assert "analytics.fct_order_items" in schemas["models"]
    model = client.get("/api/v1/schemas/analytics.fct_order_items", headers=admin).json()
    assert model["model"] == "analytics.fct_order_items"
    assert {"name": "customer_state", "type": "String"} in model["columns"]


def test_admin_can_reindex_knowledge_control_plane():
    client = TestClient(create_app(testing=True))
    analyst = auth(client, "analyst", "analyst-demo")
    admin = auth(client, "admin", "admin-demo")

    assert client.post("/api/v1/admin/knowledge/reindex", headers=analyst).status_code == 403

    response = client.post("/api/v1/admin/knowledge/reindex", headers=admin)
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["documents"] >= 10

    knowledge = client.get("/api/v1/knowledge", headers=admin).json()
    refs = {item["source_ref"] for item in knowledge["documents"]}
    assert "knowledge/business_glossary.md" in refs
    assert any(ref.startswith("metric:delivered_revenue") for ref in refs)
    assert not any(item["source_type"] == "schema" for item in knowledge["documents"])


def test_admin_can_run_real_evaluation_suite():
    client = TestClient(create_app(testing=True))
    analyst = auth(client, "analyst", "analyst-demo")
    admin = auth(client, "admin", "admin-demo")

    listed = client.get("/api/v1/evaluations", headers=analyst)
    assert listed.status_code == 200
    assert listed.json()["cases"] >= 60
    assert listed.json()["latest"] is None

    assert client.post("/api/v1/evaluations/run", headers=analyst).status_code == 403
    result = client.post("/api/v1/evaluations/run", headers=admin)
    assert result.status_code == 202
    payload = result.json()
    assert payload["suite"] == "olist-core-v1"
    assert payload["cases"] >= 60
    assert payload["passed"] + payload["failed"] == payload["cases"]
    assert payload["items"]

    listed_after = client.get("/api/v1/evaluations", headers=admin).json()
    assert listed_after["latest"]["suite"] == "olist-core-v1"


def test_chat_writes_conversation_and_query_audit():
    client = TestClient(create_app(testing=True))
    analyst = auth(client, "analyst", "analyst-demo")

    with client.stream(
        "POST",
        "/api/v1/chat/stream",
        json={"question": "2018年1月各州已交付收入是多少？", "conversation_id": "workspace"},
        headers=analyst,
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "knowledge/business_glossary.md" in body

    conversations = client.get("/api/v1/conversations", headers=analyst).json()["items"]
    assert conversations[0]["state"]["events"][0]["type"] == "user"
    assert conversations[0]["state"]["events"][1]["type"] == "assistant"

    audit = client.get("/api/v1/audit/queries", headers=analyst).json()["items"]
    assert audit[0]["normalized_sql"].startswith("SELECT")
    assert audit[0]["evidence"]["metrics"][0]["name"] == "delivered_revenue"
