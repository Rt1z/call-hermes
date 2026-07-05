# ffmpeg is invoked without a shell and only receives fixed flags plus temporary paths.
import subprocess  # nosec B404
import tempfile
import wave
import os
from pathlib import Path


def transcode_to_pcm16_mono_16k(input_bytes: bytes, suffix: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / f"input{suffix or '.webm'}"
        output_path = Path(tmpdir) / "output.pcm"
        input_path.write_bytes(input_bytes)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "s16le",
            str(output_path),
        ]
        subprocess.run(command, check=True, capture_output=True)  # nosec B603
        return output_path.read_bytes()


def transcode_to_wav_mono_16k_file(input_bytes: bytes, suffix: str) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / f"input{suffix or '.webm'}"
        input_path.write_bytes(input_bytes)
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-sample_fmt",
            "s16",
            tmp.name,
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)  # nosec B603
        except subprocess.CalledProcessError as exc:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise RuntimeError("Audio decode failed: unsupported audio format") from exc
    return tmp.name


def pcm16_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        with wave.open(tmp.name, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm)
        return Path(tmp.name).read_bytes()


def wav_duration_seconds(path: str) -> float:
    with wave.open(path, "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate()
        return frames / rate if rate else 0.0
