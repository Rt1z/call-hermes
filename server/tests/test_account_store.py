from app.account_store import AccountStore


def test_authentication_refresh_rotation_and_device_revocation(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = AccountStore(str(tmp_path / "accounts.sqlite3"))
    store.initialize("admin", "a-secure-bootstrap-password")
    user = store.authenticate("admin", "a-secure-bootstrap-password")
    assert user is not None
    assert store.authenticate("admin", "wrong") is None
    device_id = store.register_device(user["id"], "Test browser")
    token, _ = store.issue_refresh_token(user["id"], device_id, 30)
    store.revoke_device_tokens(device_id)
    assert store.consume_refresh_token(token) is None
    token, _ = store.issue_refresh_token(user["id"], device_id, 30)
    refreshed = store.consume_refresh_token(token)
    assert refreshed is not None and refreshed["device_id"] == device_id
    assert store.consume_refresh_token(token) is None
    assert store.device_active(user["id"], device_id)
    assert store.revoke_device(user["id"], device_id)
    assert not store.device_active(user["id"], device_id)


def test_users_password_and_audit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = AccountStore(str(tmp_path / "accounts.sqlite3"))
    store.initialize("admin", "a-secure-bootstrap-password")
    user = store.create_user("alice", "alice-password-is-long", "user")
    assert store.authenticate("alice", "alice-password-is-long") is not None
    assert store.change_password(user["id"], "alice-password-is-long", "new-password-is-long")
    assert store.authenticate("alice", "alice-password-is-long") is None
    assert store.authenticate("alice", "new-password-is-long") is not None
    store.audit("password_changed", user["id"], ip_address="127.0.0.1")
    assert store.list_audit(user["id"])[0]["action"] == "password_changed"
