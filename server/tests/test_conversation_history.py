import asyncio
from collections.abc import AsyncIterator

import app.bridge.session as session_module
from app.bridge.session import VoiceBridgeSession
from app.config import Settings
from app.integrations.hermes import _build_chat_messages


def _settings(**updates: object) -> Settings:
    return Settings(
        app_shared_secret="x" * 32,
        jwt_secret="y" * 32,
        **updates,
    )


class _FakeOutputTrack:
    async def push_pcm16(self, pcm: bytes, sample_rate: int) -> None:
        pass

    def finish_utterance(self) -> None:
        pass

    async def wait_until_idle(self) -> None:
        pass

    def clear(self) -> None:
        pass

    async def close_queue(self) -> None:
        pass


def test_chat_messages_include_system_history_and_current_user() -> None:
    settings = _settings(hermes_system_prompt="voice prompt")
    history = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    ]

    messages = _build_chat_messages(settings, "second question", history)

    assert messages == [
        {"role": "system", "content": "voice prompt"},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
    ]


async def test_session_history_keeps_complete_pairs_and_limits_turns() -> None:
    session = VoiceBridgeSession(
        "test-session",
        _settings(hermes_history_max_turns=2, hermes_history_max_chars=10000),
    )

    session._commit_conversation_turn("one", "answer one")
    session._commit_conversation_turn("two", "answer two")
    session._commit_conversation_turn("three", "answer three")
    history = session.conversation_history
    history[0]["content"] = "mutated copy"
    await session.close()

    assert session.conversation_history == [
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "answer two"},
        {"role": "user", "content": "three"},
        {"role": "assistant", "content": "answer three"},
    ]


async def test_session_history_limits_characters_by_whole_turn() -> None:
    session = VoiceBridgeSession(
        "test-session",
        _settings(hermes_history_max_turns=10, hermes_history_max_chars=1000),
    )

    session._commit_conversation_turn("a" * 400, "b" * 400)
    session._commit_conversation_turn("c" * 400, "d" * 400)
    await session.close()

    assert session.conversation_history == [
        {"role": "user", "content": "c" * 400},
        {"role": "assistant", "content": "d" * 400},
    ]


async def test_second_response_receives_first_completed_turn(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    received_histories: list[list[dict[str, str]]] = []

    class FakeHermesClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def stream_chat(
            self,
            user_text: str,
            history: list[dict[str, str]] | None = None,
        ) -> AsyncIterator[str]:
            received_histories.append([dict(message) for message in history or []])
            yield f"answer to {user_text}"

    class FakeTTS:
        async def synthesize_stream(self, chunks: AsyncIterator[str]) -> AsyncIterator[bytes]:
            async for _chunk in chunks:
                pass
            if False:
                yield b""

    monkeypatch.setattr(session_module, "HermesClient", FakeHermesClient)
    monkeypatch.setattr(session_module, "create_tts_session", lambda _settings: FakeTTS())
    session = VoiceBridgeSession("test-session", _settings())
    session.output_track = _FakeOutputTrack()  # type: ignore[assignment]

    await session._respond("first", None, "turn-1")
    await session._respond("second", None, "turn-2")
    turn_events = [
        event
        for event in session.events.history
        if event.type in {"thinking", "answer_delta", "speaking"}
    ]
    await session.close()

    assert received_histories == [
        [],
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer to first"},
        ],
    ]
    assert turn_events
    assert {event.payload.get("turn_id") for event in turn_events} == {"turn-1", "turn-2"}


async def test_interrupted_turn_is_available_to_next_response(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    first_started = asyncio.Event()
    received_histories: list[tuple[str, list[dict[str, str]]]] = []

    class FakeHermesClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def stream_chat(
            self,
            user_text: str,
            history: list[dict[str, str]] | None = None,
        ) -> AsyncIterator[str]:
            received_histories.append((user_text, [dict(message) for message in history or []]))
            if user_text == "first interrupted question":
                first_started.set()
                await asyncio.Event().wait()
            yield "second answer"

    class FakeTTS:
        async def synthesize_stream(self, chunks: AsyncIterator[str]) -> AsyncIterator[bytes]:
            async for _chunk in chunks:
                pass
            if False:
                yield b""

    monkeypatch.setattr(session_module, "HermesClient", FakeHermesClient)
    monkeypatch.setattr(session_module, "create_tts_session", lambda _settings: FakeTTS())
    session = VoiceBridgeSession("test-session", _settings())
    session.output_track = _FakeOutputTrack()  # type: ignore[assignment]

    session._schedule_response("first interrupted question", None, "turn-interrupted")
    await first_started.wait()
    session._schedule_response("follow-up question", None, "turn-follow-up")
    assert session._respond_task is not None
    await session._respond_task
    await session.close()

    assert received_histories[1] == (
        "follow-up question",
        [{"role": "user", "content": "first interrupted question"}],
    )


async def test_multiple_assistant_messages_are_all_sent_to_tts_and_history(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    spoken: list[str] = []

    class FakeHermesClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def stream_chat(
            self,
            _user_text: str,
            history: list[dict[str, str]] | None = None,
        ) -> AsyncIterator[str]:
            yield "\n\n第一条独立回复。"
            yield "\n\n第二条独立回复。"
            yield "\n\n第三条独立回复。"

    class FakeTTS:
        async def synthesize_stream(self, chunks: AsyncIterator[str]) -> AsyncIterator[bytes]:
            async for chunk in chunks:
                spoken.append(chunk)
            if False:
                yield b""

    monkeypatch.setattr(session_module, "HermesClient", FakeHermesClient)
    monkeypatch.setattr(session_module, "create_tts_session", lambda _settings: FakeTTS())
    session = VoiceBridgeSession("test-session", _settings())
    session.output_track = _FakeOutputTrack()  # type: ignore[assignment]

    await session._respond("question", None, "turn-multiple")
    await session.close()

    assert "".join(spoken) == "第一条独立回复。第二条独立回复。第三条独立回复。"
    assert session.conversation_history == [
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": "第一条独立回复。\n\n第二条独立回复。\n\n第三条独立回复。",
        },
    ]


async def test_barge_in_cancels_remaining_assistant_messages(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    first_consumed = asyncio.Event()
    release_second = asyncio.Event()
    spoken: list[str] = []

    class FakeHermesClient:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def stream_chat(
            self,
            _user_text: str,
            history: list[dict[str, str]] | None = None,
        ) -> AsyncIterator[str]:
            yield "第一条会播放。"
            first_consumed.set()
            await release_second.wait()
            yield "第二条不应播放。"

    class FakeTTS:
        async def synthesize_stream(self, chunks: AsyncIterator[str]) -> AsyncIterator[bytes]:
            async for chunk in chunks:
                spoken.append(chunk)
            if False:
                yield b""

    monkeypatch.setattr(session_module, "HermesClient", FakeHermesClient)
    monkeypatch.setattr(session_module, "create_tts_session", lambda _settings: FakeTTS())
    session = VoiceBridgeSession("test-session", _settings())
    session.output_track = _FakeOutputTrack()  # type: ignore[assignment]

    session._schedule_response("question", None, "turn-interrupt-multiple")
    await first_consumed.wait()
    session._interrupt_response()
    assert session._respond_task is not None
    await session._respond_task
    release_second.set()
    await session.close()

    assert "第二条不应播放。" not in "".join(spoken)
    assert session.conversation_history == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "第一条会播放。"},
    ]


async def test_assistant_echo_does_not_confirm_barge_in(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    session = VoiceBridgeSession("test-session", _settings())
    interrupted = False

    def mark_interrupted() -> None:
        nonlocal interrupted
        interrupted = True

    monkeypatch.setattr(session, "_interrupt_response", mark_interrupted)
    session._remember_assistant_text("这是助手正在朗读的完整回复。")
    session._is_speaking.set()

    accepted = session._accept_input_while_speaking("助手正在朗读")
    await session.close()

    assert accepted is False
    assert interrupted is False


async def test_distinct_user_speech_confirms_barge_in(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    session = VoiceBridgeSession("test-session", _settings())
    interrupted = False

    def mark_interrupted() -> None:
        nonlocal interrupted
        interrupted = True

    monkeypatch.setattr(session, "_interrupt_response", mark_interrupted)
    session._remember_assistant_text("这是助手正在朗读的完整回复。")
    session._is_speaking.set()

    accepted = session._accept_input_while_speaking("请先停一下")
    await session.close()

    assert accepted is True
    assert interrupted is True
