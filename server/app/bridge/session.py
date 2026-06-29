import asyncio
import json
import logging
import math
import time
from uuid import uuid4
from array import array
from collections import deque
from collections.abc import AsyncIterator

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

from app.bridge.audio import PCM16Resampler, QueueAudioTrack
from app.config import Settings
from app.events import EventSink
from app.integrations.asr import Transcript, create_asr_session
from app.integrations.hermes import HermesClient
from app.integrations.tts import create_tts_session
from app.integrations.tts_normalize import StreamingTTSNormalizer
from app.pwa.trace import TurnTrace

logger = logging.getLogger("call_hermes.bridge")


class VoiceBridgeSession:
    def __init__(
        self,
        session_id: str,
        settings: Settings,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> None:
        self.session_id = session_id
        self.settings = settings
        self.pc = RTCPeerConnection(configuration=_rtc_configuration(settings))
        self.events = EventSink()
        self.output_track = QueueAudioTrack(
            prebuffer_seconds=settings.webrtc_audio_prebuffer_seconds,
            min_prebuffer_seconds=settings.webrtc_audio_prebuffer_min_seconds,
            max_prebuffer_seconds=settings.webrtc_audio_prebuffer_max_seconds,
            logger=logger,
            session_id=session_id,
        )
        self.pc.addTrack(self.output_track)
        self._tasks: set[asyncio.Task[None]] = set()
        self._is_speaking = asyncio.Event()
        self._turn_trace: TurnTrace | None = None
        self._respond_task: asyncio.Task[None] | None = None
        self._respond_turn_id: str | None = None
        self._active_input_turn_id: str | None = None
        self._conversation_history = [dict(message) for message in conversation_history or []]
        self._trim_conversation_history()
        self._last_interrupt_at = 0.0
        self._assistant_echo_text = ""
        self._client_muted = False
        self._barge_in_confirmed = False

        @self.pc.on("datachannel")
        def on_datachannel(channel) -> None:  # type: ignore[no-untyped-def]
            if channel.label == "events":
                self.events.bind_channel(channel)
                self.events.emit("listening", session_id=self.session_id)

                @channel.on("message")
                def on_message(message) -> None:  # type: ignore[no-untyped-def]
                    task = asyncio.create_task(self._handle_client_message(message))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)

        @self.pc.on("track")
        def on_track(track) -> None:  # type: ignore[no-untyped-def]
            if track.kind == "audio":
                task = asyncio.create_task(self._consume_audio(track))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

        @self.pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            if self.pc.connectionState in {"failed", "closed", "disconnected"}:
                await self.close()

    async def answer(self, offer_sdp: str, offer_type: str) -> dict[str, str]:
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type=offer_type))
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        await _wait_for_ice_gathering(self.pc)
        return {"sdp": self.pc.localDescription.sdp, "type": self.pc.localDescription.type}

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self.output_track.close_queue()
        await self.pc.close()

    @property
    def conversation_history(self) -> list[dict[str, str]]:
        return [dict(message) for message in self._conversation_history]

    def _interrupt_response(self) -> None:
        task = self._respond_task
        if task is not None and not task.done():
            task.cancel()
        self.output_track.clear()

    async def _handle_client_message(self, message: object) -> None:
        if not isinstance(message, str):
            return
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            return
        message_type = payload.get("type")
        if message_type == "microphone_muted":
            self._client_muted = bool(payload.get("muted"))
            logger.info("session_id=%s microphone_muted=%s", self.session_id, self._client_muted)
            self.events.emit("microphone", muted=self._client_muted)
            return
        if message_type == "network_quality":
            await self._handle_network_quality(payload)
            return
        if message_type == "debug_text":
            text = str(payload.get("text") or "").strip()
            if not text:
                return
            await self.submit_text(text[:4000])

    async def _handle_network_quality(self, payload: dict[str, object]) -> None:
        if not self.settings.webrtc_adaptive_buffer_enabled:
            return
        try:
            requested_seconds = float(payload.get("prebuffer_seconds", ""))
        except (TypeError, ValueError):
            return
        if not math.isfinite(requested_seconds):
            return
        quality = str(payload.get("quality") or "unknown")
        if quality not in {"excellent", "good", "fair", "poor"}:
            quality = "unknown"
        actual_seconds = self.output_track.set_prebuffer_seconds(requested_seconds)
        logger.info(
            "session_id=%s network quality=%s prebuffer_requested=%.2f prebuffer_actual=%.2f",
            self.session_id,
            quality,
            requested_seconds,
            actual_seconds,
        )
        self.events.emit(
            "network_buffer",
            quality=quality,
            prebuffer_seconds=actual_seconds,
        )

    async def submit_text(self, text: str) -> None:
        if self._is_speaking.is_set():
            self._interrupt_response()
        turn_id = uuid4().hex
        trace = TurnTrace(turn_id=turn_id, logger=logger)
        trace.mark("asr_final")
        self.events.emit("final_transcript", text=text, turn_id=turn_id)
        task = asyncio.create_task(self._respond(text, trace, turn_id))
        self._respond_task = task
        self._respond_turn_id = turn_id
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _consume_audio(self, track) -> None:  # type: ignore[no-untyped-def]
        resampler = PCM16Resampler(target_rate=16000)
        auto_vad_enabled = self.settings.auto_vad_enabled
        vad_threshold = self.settings.auto_vad_rms_threshold
        silence_seconds = self.settings.auto_vad_silence_ms / 1000
        min_speech_seconds = self.settings.auto_vad_min_speech_ms / 1000
        vad_active = not auto_vad_enabled
        speech_started_at: float | None = None
        last_voice_at = time.monotonic()
        vad_preroll: deque[bytes] = deque()
        vad_preroll_bytes = 0
        vad_preroll_max_bytes = 16000 * 2 * self.settings.auto_vad_preroll_ms // 1000

        def on_transcript(transcript: Transcript) -> None:
            text = transcript.text.strip()
            if not self._accept_input_while_speaking(text):
                if transcript.is_final:
                    logger.info(
                        "session_id=%s ignored unconfirmed barge-in final text=%r",
                        self.session_id,
                        text[:40],
                    )
                return
            if self._active_input_turn_id is None:
                self._active_input_turn_id = uuid4().hex
            turn_id = self._active_input_turn_id
            if self._turn_trace is None:
                self._turn_trace = TurnTrace(turn_id=turn_id, logger=logger)
                self._turn_trace.mark("asr_first_partial")
            self.events.emit(
                "final_transcript" if transcript.is_final else "partial_transcript",
                text=transcript.text,
                turn_id=turn_id,
            )
            if transcript.is_final:
                self._barge_in_confirmed = False
                self._active_input_turn_id = None
                if self._turn_trace is not None:
                    self._turn_trace.mark("asr_final")
                trace = self._turn_trace
                self._turn_trace = None
                task = asyncio.create_task(self._respond(transcript.text, trace, turn_id))
                self._respond_task = task
                self._respond_turn_id = turn_id
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

        def on_error(message: str) -> None:
            self.events.emit("error", source="asr", message=message)

        asr = None

        async def start_asr_if_needed() -> None:
            nonlocal asr
            if asr is not None:
                return
            asr = create_asr_session(self.settings, on_transcript, on_error)
            await asr.start()
            logger.info("session_id=%s asr stream started", self.session_id)
            self.events.emit("asr_state", state="started")

        async def stop_asr_if_needed(reason: str) -> None:
            nonlocal asr
            if asr is None:
                return
            await asr.stop()
            asr = None
            logger.info("session_id=%s asr stream stopped reason=%s", self.session_id, reason)
            self.events.emit("asr_state", state="stopped", reason=reason)

        try:
            while True:
                frame = await track.recv()
                if self._client_muted:
                    await stop_asr_if_needed("microphone_muted")
                    vad_preroll.clear()
                    vad_preroll_bytes = 0
                    speech_started_at = None
                    if vad_active:
                        vad_active = False
                        self.events.emit("vad_state", state="muted")
                    continue
                pcm = resampler.resample_to_pcm16(frame)
                if not pcm:
                    continue
                if auto_vad_enabled:
                    now = time.monotonic()
                    rms = _normalized_pcm16_rms(pcm)
                    vad_preroll.append(pcm)
                    vad_preroll_bytes += len(pcm)
                    while vad_preroll_bytes > vad_preroll_max_bytes and len(vad_preroll) > 1:
                        vad_preroll_bytes -= len(vad_preroll.popleft())
                    if rms >= vad_threshold:
                        last_voice_at = now
                        if speech_started_at is None:
                            speech_started_at = now
                        if not vad_active and now - speech_started_at >= min_speech_seconds:
                            vad_active = True
                            logger.info(
                                "session_id=%s vad_state=speech rms=%.4f threshold=%.4f",
                                self.session_id,
                                rms,
                                vad_threshold,
                            )
                            self.events.emit("vad_state", state="speech")
                            await start_asr_if_needed()
                            while vad_preroll:
                                preroll_chunk = vad_preroll.popleft()
                                vad_preroll_bytes -= len(preroll_chunk)
                                await asr.send_pcm16(preroll_chunk)
                            continue
                    else:
                        speech_started_at = None
                    if vad_active and now - last_voice_at >= silence_seconds:
                        vad_active = False
                        logger.info(
                            "session_id=%s vad_state=silence rms=%.4f threshold=%.4f",
                            self.session_id,
                            rms,
                            vad_threshold,
                        )
                        self.events.emit("vad_state", state="silence")
                        await stop_asr_if_needed("vad_silence")
                    if not vad_active:
                        continue
                await start_asr_if_needed()
                await asr.send_pcm16(pcm)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.events.emit("error", source="webrtc_audio", message=str(exc))
        finally:
            await stop_asr_if_needed("audio_consumer_closed")

    def _should_interrupt(self, text: str) -> bool:
        if not self._is_speaking.is_set():
            return False
        if len(text) < self.settings.barge_in_min_chars:
            return False
        if self._looks_like_assistant_echo(text):
            logger.info(
                "session_id=%s ignored assistant echo text_len=%d text=%r",
                self.session_id,
                len(text),
                text[:40],
            )
            return False
        now = time.monotonic()
        cooldown_seconds = self.settings.barge_in_cooldown_ms / 1000
        if now - self._last_interrupt_at < cooldown_seconds:
            return False
        self._last_interrupt_at = now
        return True

    def _accept_input_while_speaking(self, text: str) -> bool:
        if not self._is_speaking.is_set() or self._barge_in_confirmed:
            return True
        if not self._should_interrupt(text):
            return False
        self._barge_in_confirmed = True
        logger.info(
            "session_id=%s barge-in confirmed text_len=%d text=%r",
            self.session_id,
            len(text),
            text[:40],
        )
        self._interrupt_response()
        return True

    def _remember_assistant_text(self, text: str) -> None:
        self._assistant_echo_text = (self._assistant_echo_text + text)[-1200:]

    def _looks_like_assistant_echo(self, text: str) -> bool:
        heard = _normalize_for_echo_match(text)
        spoken = _normalize_for_echo_match(self._assistant_echo_text)
        return len(heard) >= self.settings.barge_in_min_chars and heard in spoken

    async def _respond(self, user_text: str, trace: TurnTrace | None, turn_id: str) -> None:
        self._is_speaking.set()
        self._barge_in_confirmed = False
        self._assistant_echo_text = ""
        self.events.emit("thinking", text=user_text, turn_id=turn_id)
        hermes = HermesClient(self.settings)
        normalizer = StreamingTTSNormalizer()
        hermes_first_marked = False
        tts_first_marked = False
        hermes_completed = False
        assistant_text = ""

        async def hermes_chunks() -> AsyncIterator[str]:
            nonlocal assistant_text, hermes_completed, hermes_first_marked
            buffer = ""
            try:
                async for chunk in hermes.stream_chat(
                    user_text,
                    history=self._conversation_history,
                ):
                    if not hermes_first_marked and trace is not None:
                        trace.mark("hermes_first_token")
                        hermes_first_marked = True
                    self._remember_assistant_text(chunk)
                    assistant_text += chunk
                    self.events.emit("answer_delta", text=chunk, turn_id=turn_id)
                    buffer += chunk
                    if _should_flush(buffer):
                        normalized = normalizer.feed(buffer) + normalizer.flush()
                        if normalized:
                            yield normalized
                        buffer = ""
                if buffer:
                    normalized = normalizer.feed(buffer) + normalizer.flush()
                    if normalized:
                        yield normalized
                hermes_completed = True
            except Exception as exc:  # noqa: BLE001
                self.events.emit("error", source="hermes", message=str(exc))
                fallback_text = "Hermes 暂时无法连接，请稍后再试。"
                self.events.emit("answer_delta", text=fallback_text, turn_id=turn_id)
                yield fallback_text

        try:
            self.events.emit("speaking", state="start", turn_id=turn_id)
            hermes_iter = hermes_chunks()
            try:
                first_tts_text = await asyncio.wait_for(
                    anext(hermes_iter),
                    timeout=self.settings.hermes_timeout_seconds + 5,
                )
            except StopAsyncIteration:
                first_tts_text = "Hermes 没有返回内容。"
                self.events.emit("answer_delta", text=first_tts_text, turn_id=turn_id)

            async def tts_text_chunks() -> AsyncIterator[str]:
                yield first_tts_text
                async for text in hermes_iter:
                    yield text

            tts = create_tts_session(self.settings)
            tts_bytes = 0
            tts_chunks = 0
            async for pcm24k in tts.synthesize_stream(tts_text_chunks()):
                if not tts_first_marked:
                    if trace is not None:
                        trace.mark("tts_first_audio")
                    tts_first_marked = True
                tts_chunks += 1
                tts_bytes += len(pcm24k)
                await self.output_track.push_pcm16(pcm24k, sample_rate=24000)
            if hermes_completed and assistant_text.strip():
                self._commit_conversation_turn(user_text, assistant_text)
            self.output_track.finish_utterance()
            logger.info(
                "session_id=%s tts stream complete chunks=%d pcm24k_bytes=%d",
                self.session_id,
                tts_chunks,
                tts_bytes,
            )
            await self.output_track.wait_until_idle()
            logger.info("session_id=%s audio playback drained", self.session_id)
            if trace is not None:
                trace.mark("speaking_end")
            self.events.emit("speaking", state="end", turn_id=turn_id)
        except asyncio.CancelledError:
            self.output_track.clear()
            self.events.emit("speaking", state="interrupted", turn_id=turn_id)
        except Exception as exc:  # noqa: BLE001
            self.output_track.clear()
            self.events.emit("error", source="tts", message=str(exc))
        finally:
            if self._respond_task is asyncio.current_task():
                self._is_speaking.clear()
                self.events.emit("listening", session_id=self.session_id)
                self._respond_task = None
                self._respond_turn_id = None
            if self._turn_trace is trace:
                self._turn_trace = None
            if trace is not None:
                logger.info(
                    "session_id=%s p2p turn complete transcript=%r hermes_ttft=%sms tts_ttfa=%sms "
                    "respond_total=%sms summary=%s",
                    self.session_id,
                    user_text[:80],
                    trace.gap("asr_final", "hermes_first_token"),
                    trace.gap("hermes_first_token", "tts_first_audio"),
                    trace.gap("asr_final", "speaking_end"),
                    trace.summary(),
                )

    def _commit_conversation_turn(self, user_text: str, assistant_text: str) -> None:
        self._conversation_history.extend(
            [
                {"role": "user", "content": user_text.strip()},
                {"role": "assistant", "content": assistant_text.strip()},
            ]
        )
        self._trim_conversation_history()
        logger.info(
            "session_id=%s conversation committed messages=%d chars=%d",
            self.session_id,
            len(self._conversation_history),
            sum(len(message["content"]) for message in self._conversation_history),
        )

    def _trim_conversation_history(self) -> None:
        max_messages = self.settings.hermes_history_max_turns * 2
        while len(self._conversation_history) > max_messages:
            del self._conversation_history[:2]
        while (
            len(self._conversation_history) > 2
            and sum(len(message.get("content", "")) for message in self._conversation_history)
            > self.settings.hermes_history_max_chars
        ):
            del self._conversation_history[:2]


def _should_flush(text: str) -> bool:
    if len(text) >= 24:
        return True
    return any(text.endswith(mark) for mark in ("。", "！", "？", ".", "!", "?", "\n"))


def _normalize_for_echo_match(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _normalized_pcm16_rms(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return 0.0
    total = sum(sample * sample for sample in samples)
    return (total / len(samples)) ** 0.5 / 32768


def _rtc_configuration(settings: Settings) -> RTCConfiguration:
    ice_servers = [
        RTCIceServer(
            urls=server["urls"],  # type: ignore[arg-type]
            username=str(server.get("username") or "") or None,
            credential=str(server.get("credential") or "") or None,
        )
        for server in settings.server_ice_servers
    ]
    return RTCConfiguration(iceServers=ice_servers)


async def _wait_for_ice_gathering(pc: RTCPeerConnection, timeout_seconds: float = 5.0) -> None:
    if pc.iceGatheringState == "complete":
        return
    completed = asyncio.Event()

    @pc.on("icegatheringstatechange")
    def on_ice_gathering_state_change() -> None:
        if pc.iceGatheringState == "complete":
            completed.set()

    try:
        await asyncio.wait_for(completed.wait(), timeout=timeout_seconds)
    except TimeoutError:
        logger.warning("ice gathering timed out state=%s", pc.iceGatheringState)
