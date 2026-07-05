import numpy as np

from app.bridge.audio import QueueAudioTrack


async def test_playback_rebuffers_instead_of_alternating_audio_and_silence() -> None:
    underruns = 0

    def on_underrun() -> None:
        nonlocal underruns
        underruns += 1

    track = QueueAudioTrack(prebuffer_seconds=0, on_underrun=on_underrun)
    pcm = np.zeros(960, dtype=np.int16).tobytes()

    await track.push_pcm16(pcm, sample_rate=48000)
    await track.recv()

    assert track._playing_audio is True
    assert track._idle_event.is_set() is False

    await track.recv()

    assert track._playing_audio is False
    assert track._underrun_frames == 1
    assert track._rebuffer_count == 1
    assert underruns == 1
    assert track.prebuffer_seconds == 0.2

    track.finish_utterance()
    await track.recv()

    assert track._playing_audio is False
    assert track._idle_event.is_set() is True


async def test_playback_resumes_after_prebuffer_refills() -> None:
    track = QueueAudioTrack(prebuffer_seconds=0.04, rebuffer_step_seconds=0)
    pcm = np.zeros(1920, dtype=np.int16).tobytes()

    await track.push_pcm16(pcm, sample_rate=48000)
    await track.recv()
    await track.recv()
    await track.recv()

    assert track._playing_audio is False
    assert track._rebuffer_count == 1

    await track.push_pcm16(pcm, sample_rate=48000)
    await track.recv()

    assert track._playing_audio is True


def test_prebuffer_update_is_clamped_and_updates_bytes() -> None:
    track = QueueAudioTrack(
        sample_rate=48000,
        prebuffer_seconds=0.5,
        min_prebuffer_seconds=0.3,
        max_prebuffer_seconds=1.0,
    )

    assert track.set_prebuffer_seconds(0.1) == 0.3
    assert track.prebuffer_bytes == 48000 * 2 * 0.3
    assert track.set_prebuffer_seconds(1.5) == 1.0
    assert track.prebuffer_bytes == 48000 * 2
