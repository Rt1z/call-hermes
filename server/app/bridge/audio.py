import asyncio
import logging
import math
import time
from fractions import Fraction

import av
import numpy as np
from aiortc import AudioStreamTrack
from aiortc.mediastreams import MediaStreamError
from av.audio.resampler import AudioResampler


class PCM16Resampler:
    def __init__(self, target_rate: int) -> None:
        self._resampler = AudioResampler(format="s16", layout="mono", rate=target_rate)

    def resample_to_pcm16(self, frame: av.AudioFrame) -> bytes:
        chunks: list[bytes] = []
        for out in self._resampler.resample(frame):
            chunks.append(out.to_ndarray().tobytes())
        return b"".join(chunks)


class QueueAudioTrack(AudioStreamTrack):
    kind = "audio"

    def __init__(
        self,
        sample_rate: int = 48000,
        frame_samples: int = 960,
        prebuffer_seconds: float = 0.5,
        min_prebuffer_seconds: float = 0.0,
        max_prebuffer_seconds: float = 1.0,
        logger: logging.Logger | None = None,
        session_id: str = "-",
    ) -> None:
        super().__init__()
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.min_prebuffer_seconds = min(min_prebuffer_seconds, max_prebuffer_seconds)
        self.max_prebuffer_seconds = max(min_prebuffer_seconds, max_prebuffer_seconds)
        self.prebuffer_seconds = 0.0
        self.prebuffer_bytes = 0
        self._logger = logger
        self._session_id = session_id
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)
        self._resampler = AudioResampler(format="s16", layout="mono", rate=sample_rate)
        self._buffer = bytearray()
        self._pts = 0
        self._started_at: float | None = None
        self._closed = False
        self._playing_audio = False
        self._flush_audio = False
        self._queued_bytes = 0
        self._played_bytes = 0
        self._underrun_frames = 0
        self._rebuffer_count = 0
        self._started_playback_at: float | None = None
        self._idle_event = asyncio.Event()
        self._idle_event.set()
        self.set_prebuffer_seconds(prebuffer_seconds)

    def set_prebuffer_seconds(self, seconds: float) -> float:
        if not math.isfinite(seconds):
            raise ValueError("prebuffer seconds must be finite")
        normalized = min(self.max_prebuffer_seconds, max(self.min_prebuffer_seconds, seconds))
        self.prebuffer_seconds = normalized
        self.prebuffer_bytes = int(self.sample_rate * 2 * normalized)
        if self._logger:
            self._logger.info(
                "session_id=%s audio prebuffer updated seconds=%.2f bytes=%d playing=%s",
                self._session_id,
                normalized,
                self.prebuffer_bytes,
                self._playing_audio,
            )
        return normalized

    async def push_pcm16(self, pcm: bytes, sample_rate: int) -> None:
        self._idle_event.clear()
        frame = _pcm_to_frame(pcm, sample_rate)
        for resampled in self._resampler.resample(frame):
            data = resampled.to_ndarray().tobytes()
            self._queued_bytes += len(data)
            await self._queue.put(data)

    def finish_utterance(self) -> None:
        self._flush_audio = True
        if not self._playing_audio and not self._buffer and self._queue.empty():
            self._idle_event.set()
        if self._logger:
            self._logger.info(
                "session_id=%s audio finish queued_bytes=%d buffered_bytes=%d queue_size=%d",
                self._session_id,
                self._queued_bytes,
                len(self._buffer),
                self._queue.qsize(),
            )

    def clear(self) -> None:
        if self._logger and (self._buffer or self._queue.qsize()):
            self._logger.info(
                "session_id=%s audio clear buffered_bytes=%d queue_size=%d played_bytes=%d underruns=%d",
                self._session_id,
                len(self._buffer),
                self._queue.qsize(),
                self._played_bytes,
                self._underrun_frames,
            )
        self._buffer.clear()
        self._playing_audio = False
        self._flush_audio = False
        self._queued_bytes = 0
        self._played_bytes = 0
        self._underrun_frames = 0
        self._rebuffer_count = 0
        self._started_playback_at = None
        self._idle_event.set()
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def close_queue(self) -> None:
        self._closed = True
        self._idle_event.set()
        await self._queue.put(None)

    async def wait_until_idle(self) -> None:
        await self._idle_event.wait()

    async def recv(self) -> av.AudioFrame:
        if self.readyState != "live" or self._closed:
            raise MediaStreamError

        if self._started_at is None:
            self._started_at = time.monotonic()
        else:
            next_time = self._started_at + (self._pts / self.sample_rate)
            now = time.monotonic()
            frame_seconds = self.frame_samples / self.sample_rate
            if now - next_time > frame_seconds * 2:
                # Do not burst stale RTP frames after an event-loop or network stall.
                self._started_at = now - (self._pts / self.sample_rate)
                next_time = now
            await asyncio.sleep(max(0, next_time - now))

        self._drain_queue()
        bytes_needed = self.frame_samples * 2
        has_enough_prebuffer = len(self._buffer) >= self.prebuffer_bytes
        if self._buffer and not self._playing_audio and (has_enough_prebuffer or self._flush_audio):
            self._playing_audio = True
            if self._started_playback_at is None:
                self._started_playback_at = time.monotonic()
            if self._logger:
                self._logger.info(
                    "session_id=%s audio playback start buffered_ms=%d flush=%s queue_size=%d",
                    self._session_id,
                    int(len(self._buffer) / (self.sample_rate * 2) * 1000),
                    self._flush_audio,
                    self._queue.qsize(),
                )

        if self._playing_audio and len(self._buffer) < bytes_needed and not self._flush_audio:
            self._playing_audio = False
            self._underrun_frames += 1
            self._rebuffer_count += 1
            if self._logger:
                self._logger.warning(
                    "session_id=%s audio underrun rebuffer=%d buffered_bytes=%d target_bytes=%d",
                    self._session_id,
                    self._rebuffer_count,
                    len(self._buffer),
                    self.prebuffer_bytes,
                )

        if self._playing_audio and (len(self._buffer) >= bytes_needed or self._flush_audio):
            pcm = bytes(self._buffer[:bytes_needed])
            del self._buffer[:bytes_needed]
            if len(pcm) < bytes_needed:
                pcm += b"\x00" * (bytes_needed - len(pcm))
            self._played_bytes += bytes_needed
        else:
            pcm = b"\x00" * bytes_needed

        if self._playing_audio and self._flush_audio and not self._buffer and self._queue.empty():
            if self._logger:
                elapsed_ms = (
                    int((time.monotonic() - self._started_playback_at) * 1000)
                    if self._started_playback_at
                    else 0
                )
                self._logger.info(
                    "session_id=%s audio playback end elapsed_ms=%d played_bytes=%d "
                    "underruns=%d rebuffers=%d",
                    self._session_id,
                    elapsed_ms,
                    self._played_bytes,
                    self._underrun_frames,
                    self._rebuffer_count,
                )
            self._playing_audio = False
            self._flush_audio = False
            self._queued_bytes = 0
            self._played_bytes = 0
            self._underrun_frames = 0
            self._rebuffer_count = 0
            self._started_playback_at = None
            self._idle_event.set()

        frame = _pcm_to_frame(pcm, self.sample_rate)
        frame.pts = self._pts
        frame.time_base = Fraction(1, self.sample_rate)
        self._pts += self.frame_samples
        return frame

    def _drain_queue(self) -> None:
        while True:
            try:
                chunk = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if chunk is None:
                self._closed = True
                raise MediaStreamError
            self._buffer.extend(chunk)


def _pcm_to_frame(pcm: bytes, sample_rate: int) -> av.AudioFrame:
    samples = np.frombuffer(pcm, dtype=np.int16).reshape(1, -1)
    frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
    frame.sample_rate = sample_rate
    return frame
