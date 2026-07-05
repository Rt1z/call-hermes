from fastapi.testclient import TestClient

import app.main as main_module
from app.account_store import AccountStore
from app.config import Settings, get_settings


def test_login_refresh_and_device_revocation(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(
        app_shared_secret="x" * 32,
        jwt_secret="y" * 32,
        public_base_url="http://testserver",
        conversation_database_path=str(tmp_path / "app.sqlite3"),
    )
    store = AccountStore(settings.conversation_database_path)
    store.initialize("admin", settings.app_shared_secret)
    monkeypatch.setattr(main_module, "account_store", store)
    main_module.app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(main_module.app) as client:
            monkeypatch.setattr(main_module, "account_store", store)
            legacy = client.post(
                "/auth/session", json={"shared_secret": settings.app_shared_secret}
            )
            assert legacy.status_code == 410
            login = client.post(
                "/auth/login",
                json={
                    "username": "admin",
                    "password": settings.app_shared_secret,
                    "device_name": "Test browser",
                },
            )
            assert login.status_code == 200
            assert "HttpOnly" in login.headers["set-cookie"]
            identity = login.json()
            refresh = client.post("/auth/refresh")
            assert refresh.status_code == 200
            access_token = refresh.json()["token"]
            headers = {"Authorization": f"Bearer {access_token}"}
            assert client.get("/auth/devices", headers=headers).status_code == 200
            assert (
                client.delete(f"/auth/devices/{identity['device_id']}", headers=headers).status_code
                == 200
            )
            assert client.get("/rtc/config", headers=headers).status_code == 401
    finally:
        main_module.app.dependency_overrides.clear()
