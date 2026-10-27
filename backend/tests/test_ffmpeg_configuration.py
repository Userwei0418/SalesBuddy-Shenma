"""Executable selection and dependency failure classification, without a model call."""
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from sales_backend.api import audio
from sales_backend.integrations import ffmpeg
from sales_backend.integrations.ffmpeg_config import (
    DEFAULT_FFMPEG_EXECUTABLE,
    FfmpegUnavailable,
    validate_ffmpeg_executable,
)
from sales_backend.services.visit_import import import_failure_message


def test_linux_default_is_preserved_and_override_is_explicit(monkeypatch, tmp_path):
    assert DEFAULT_FFMPEG_EXECUTABLE == "/usr/bin/ffmpeg"
    observed = []
    monkeypatch.setattr(ffmpeg, "validate_ffmpeg_executable", lambda value: observed.append(value) or value)
    monkeypatch.delenv("FFMPEG_EXECUTABLE", raising=False)
    assert ffmpeg.resolve_ffmpeg_executable() == DEFAULT_FFMPEG_EXECUTABLE
    configured = str(tmp_path / "managed binary" / "ffmpeg")
    monkeypatch.setenv("FFMPEG_EXECUTABLE", configured)
    assert ffmpeg.resolve_ffmpeg_executable() == configured
    assert observed == [DEFAULT_FFMPEG_EXECUTABLE, configured]


@pytest.mark.parametrize("value", [
    None, "", "ffmpeg", "~/ffmpeg", "/bin/sh", "/usr/bin/ffmpeg -version", "/not-used/ffmpeg\x00",
])
def test_not_a_managed_executable_path(value):
    with pytest.raises(FfmpegUnavailable):
        validate_ffmpeg_executable(value)


def test_missing_non_executable_and_directory_are_dependency_failures(tmp_path):
    executable = tmp_path / "ffmpeg"
    with pytest.raises(FfmpegUnavailable):
        validate_ffmpeg_executable(str(executable))
    executable.write_text("test")
    executable.chmod(0o600)
    with pytest.raises(FfmpegUnavailable):
        validate_ffmpeg_executable(str(executable))
    executable.unlink()
    executable.mkdir()
    with pytest.raises(FfmpegUnavailable):
        validate_ffmpeg_executable(str(executable))


@pytest.mark.asyncio
async def test_absolute_path_with_spaces_preserves_arguments_without_shell(monkeypatch, tmp_path):
    executable = tmp_path / "managed binary" / "ffmpeg"
    executable.parent.mkdir()
    executable.symlink_to(sys.executable)
    monkeypatch.setenv("FFMPEG_EXECUTABLE", str(executable))
    marker = tmp_path / "must-not-exist"
    argument = f"$(touch {marker}); raw text"
    result = await ffmpeg.run_ffmpeg("-c", "import sys;sys.stderr.write(sys.argv[1])", argument, timeout=2)
    assert result.returncode == 0 and result.stderr.decode() == argument
    assert not marker.exists()


@pytest.mark.asyncio
async def test_dependency_disappearing_after_validation_has_safe_diagnostic(monkeypatch):
    monkeypatch.setattr(ffmpeg, "resolve_ffmpeg_executable", lambda: "/private/not-found/ffmpeg")
    with pytest.raises(FfmpegUnavailable) as error:
        await ffmpeg.run_ffmpeg("-version", timeout=2)
    assert "/private/" not in str(error.value)
    message = import_failure_message(error.value)
    assert "FFmpeg" in message and "原材料已保留" in message


@pytest.mark.asyncio
async def test_audio_http_reports_dependency_503_before_provider_call(monkeypatch):
    @asynccontextmanager
    async def transaction(*args, **kwargs):
        yield object()

    async def allow(*args):
        pass

    async def unavailable(**kwargs):
        raise FfmpegUnavailable()

    async def no_provider(*args, **kwargs):
        raise AssertionError("No provider should be called without the transcoder")

    app = FastAPI()
    app.include_router(audio.router)
    app.dependency_overrides[audio.get_database] = lambda: SimpleNamespace(transaction=transaction)
    app.dependency_overrides[audio.get_identity] = lambda: SimpleNamespace(actor=object())
    app.dependency_overrides[audio.get_settings] = lambda: object()
    monkeypatch.setattr(audio, "require_audio_purpose", allow)
    monkeypatch.setattr(audio, "transcode_for_asr", unavailable)
    monkeypatch.setattr(audio, "load_runtime_configuration", no_provider)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/audio/transcriptions", data={"purpose": "visit_entry"},
                                     files={"file": ("recording.wav", b"sample", "audio/wav")})
    assert response.status_code == 503
    assert response.json() == {"detail": "AUDIO_TRANSCODER_UNAVAILABLE"}
