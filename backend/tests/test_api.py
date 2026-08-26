from fastapi.testclient import TestClient

from app.main import create_app


def test_login_and_list_metrics():
    client = TestClient(create_app(testing=True))
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "analyst", "password": "analyst-demo"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    metrics = client.get(
        "/api/v1/metrics",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert metrics.status_code == 200
    assert any(item["name"] == "delivered_revenue" for item in metrics.json())


def test_health_live_is_public():
    client = TestClient(create_app(testing=True))
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

