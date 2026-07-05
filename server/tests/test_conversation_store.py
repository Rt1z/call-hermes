import sqlite3

from app.conversation_store import ConversationStore


def test_conversation_store_round_trip_and_clear(tmp_path) -> None:
    store = ConversationStore(str(tmp_path / "conversations.sqlite3"))
    store.initialize()
    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你？"},
    ]

    store.save("conversation-1", messages)

    assert store.load("conversation-1") == messages
    store.clear("conversation-1")
    assert store.load("conversation-1") == []


def test_conversation_store_filters_invalid_messages(tmp_path) -> None:
    store = ConversationStore(str(tmp_path / "conversations.sqlite3"))
    store.initialize()

    store.save(
        "conversation-1",
        [
            {"role": "system", "content": "hidden"},
            {"role": "user", "content": "valid"},
            {"role": "assistant", "content": ""},
        ],
    )

    assert store.load("conversation-1") == [{"role": "user", "content": "valid"}]


def test_conversation_store_tolerates_corrupt_payload(tmp_path) -> None:
    path = tmp_path / "conversations.sqlite3"
    store = ConversationStore(str(path))
    store.initialize()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO conversations (conversation_id, messages_json) VALUES (?, ?)",
            ("broken", "not-json"),
        )

    assert store.load("broken") == []


def test_conversation_store_lists_searches_and_limits(tmp_path) -> None:
    store = ConversationStore(str(tmp_path / "conversations.sqlite3"))
    store.initialize()
    store.save("one", [{"role": "user", "content": "周末安排"}])
    store.save("two", [{"role": "user", "content": "工作计划"}])

    assert {item["conversation_id"] for item in store.list()} == {"one", "two"}
    assert store.list(query="周末")[0]["conversation_id"] == "one"
    assert store.list(query="不存在") == []
    assert len(store.list(limit=1)) == 1


def test_conversation_store_health(tmp_path) -> None:
    store = ConversationStore(str(tmp_path / "conversations.sqlite3"))
    store.initialize()

    assert store.health() == (True, "ok")


def test_conversations_are_isolated_by_owner_and_metadata(tmp_path) -> None:
    store = ConversationStore(str(tmp_path / "conversations.sqlite3"))
    store.initialize()
    messages = [{"role": "user", "content": "private message"}]
    store.save("conversation-1", messages, "alice")
    assert store.load("conversation-1", "alice") == messages
    assert store.load("conversation-1", "bob") == []
    assert store.list(owner_id="bob") == []
    assert store.update_metadata("conversation-1", "alice", title="Renamed", favorite=True)
    summary = store.list(owner_id="alice")[0]
    assert summary["title"] == "Renamed"
    assert summary["favorite"] is True


def test_conversation_list_supports_pagination(tmp_path) -> None:
    store = ConversationStore(str(tmp_path / "conversations.sqlite3"))
    store.initialize()
    for index in range(5):
        store.save(f"conversation-{index}", [{"role": "user", "content": str(index)}])
    first_page = store.list(limit=2)
    second_page = store.list(limit=2, offset=2)
    assert len(first_page) == 2
    assert len(second_page) == 2
    assert {item["conversation_id"] for item in first_page}.isdisjoint(
        {item["conversation_id"] for item in second_page}
    )
