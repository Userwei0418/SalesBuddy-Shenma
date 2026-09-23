from __future__ import annotations

from pathlib import Path, PurePath
from tempfile import TemporaryDirectory

from sales_backend.async_resources import file_io
from sales_backend.integrations.ffmpeg import run_ffmpeg
from sales_backend.integrations.senseaudio import AUDIO_MIME_BY_SUFFIX, MAX_ASR_FILE_BYTES

ASR_SAMPLE_RATE = 16_000


class AudioTranscodeError(RuntimeError):
    pass


async def transcode_for_asr(*, filename: str, content: bytes) -> tuple[str, bytes, str]:
    """Decode client-specific audio and emit a stable 16 kHz mono WAV for ASR."""
    suffix = PurePath(filename or "recording.mp3").suffix.lower()
    if suffix not in AUDIO_MIME_BY_SUFFIX:
        suffix = ".mp3"

    with TemporaryDirectory(prefix="sales-asr-") as temp_dir:
        source_path = Path(temp_dir) / f"source{suffix}"
        target_path = Path(temp_dir) / "recording.wav"
        await file_io(source_path.write_bytes, content)
        try:
            result = await run_ffmpeg(
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(ASR_SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                str(target_path),
                timeout=45,
            )
        except FileNotFoundError as exc:
            raise AudioTranscodeError("audio processor is unavailable") from exc
        except TimeoutError as exc:
            raise AudioTranscodeError("audio conversion timed out") from exc
        if result.returncode != 0 or not target_path.exists():
            detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
            safe_detail = detail[-1][:240] if detail else "invalid audio stream"
            raise AudioTranscodeError(f"audio conversion failed: {safe_detail}")

        normalized = await file_io(target_path.read_bytes)
        if not normalized.startswith(b"RIFF") or normalized[8:12] != b"WAVE":
            raise AudioTranscodeError("audio conversion did not produce WAV")
        if len(normalized) > MAX_ASR_FILE_BYTES:
            raise AudioTranscodeError("audio duration exceeds the transcription limit")
        return "recording.wav", normalized, "audio/wav"
