from fastapi.testclient import TestClient

from app.main import app


def test_client_log_accepts_frontend_diagnostics() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/client/log",
            json={
                "level": "error",
                "message": "fetch failed",
                "details": {"url": "/rtc/config", "status": 503},
            },
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_client_log_rejects_oversized_message() -> None:
    with TestClient(app) as client:
        response = client.post("/client/log", json={"message": "x" * 501})

    assert response.status_code == 422


def test_client_log_rejects_oversized_details() -> None:
    with TestClient(app) as client:
        response = client.post("/client/log", json={"message": "large", "details": {"value": "x" * 9000}})

    assert response.status_code == 413
