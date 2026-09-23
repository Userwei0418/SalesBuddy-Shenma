"""Cancellation must join actual owned subprocesses and threaded file writes."""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from sales_backend.async_resources import file_io
from sales_backend.integrations import audio_codec, ffmpeg
from sales_backend.services import visit_import

pytestmark = pytest.mark.asyncio


async def wait_for_file(path):
    async with asyncio.timeout(3):
        while not path.exists():
            await asyncio.sleep(0.01)


def assert_reaped(pid):
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("operation", ["cancel", "timeout"])
async def test_real_process_is_killed_and_reaped(monkeypatch, tmp_path, operation):
    monkeypatch.setattr(ffmpeg, "FFMPEG_EXECUTABLE", sys.executable)
    marker = tmp_path / "pid"
    script = "import os,time,pathlib; pathlib.Path(__import__('sys').argv[1]).write_text(str(os.getpid()));time.sleep(30)"
    task = asyncio.create_task(ffmpeg.run_ffmpeg("-c", script, str(marker), timeout=0.25 if operation == "timeout" else 5))
    try:
        await wait_for_file(marker)
        if operation == "cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError if operation == "cancel" else TimeoutError):
            await asyncio.wait_for(task, 3)
        assert_reaped(int(marker.read_text()))
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_cancel_at_spawn_return_boundary_owns_new_child(monkeypatch, tmp_path):
    marker = tmp_path / "pid"
    real_spawn = asyncio.create_subprocess_exec
    created = asyncio.Event()
    release = asyncio.Event()
    processes = []
    async def delayed_spawn(*args, **kwargs):
        process = await real_spawn(sys.executable, "-c", "import time;time.sleep(30)", **kwargs)
        processes.append(process)
        marker.write_text(str(process.pid))
        created.set()
        await release.wait()
        return process
    monkeypatch.setattr(ffmpeg.asyncio, "create_subprocess_exec", delayed_spawn)
    task = asyncio.create_task(ffmpeg.run_ffmpeg("unused", timeout=5))
    try:
        await asyncio.wait_for(created.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()  # cannot drop spawn ownership before it returns
        task.cancel()  # shutdown can cancel cleanup more than once
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert_reaped(processes[0].pid)
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_process_result_and_stderr_are_returned(monkeypatch):
    monkeypatch.setattr(ffmpeg, "FFMPEG_EXECUTABLE", sys.executable)
    result = await ffmpeg.run_ffmpeg("-c", "import sys;sys.stderr.write('invalid stream');sys.exit(7)", timeout=2)
    assert result.returncode == 7 and result.stderr == b"invalid stream"


async def test_threaded_io_is_joined_before_directory_cleanup(tmp_path):
    started, release = threading.Event(), threading.Event()
    directory = None
    def slow_write(path):
        started.set()
        assert release.wait(3)
        path.write_bytes(b"complete")
    async def convert():
        nonlocal directory
        with TemporaryDirectory(dir=tmp_path) as folder:
            directory = Path(folder)
            await file_io(slow_write, directory / "source")
    task = asyncio.create_task(convert())
    try:
        async with asyncio.timeout(2):
            while not started.is_set():
                await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0)
        assert directory.exists() and not task.done()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert not directory.exists()
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("kind", ["short", "long"])
async def test_audio_converter_cancellation_removes_working_directory(monkeypatch, tmp_path, kind):
    real_spawn = asyncio.create_subprocess_exec
    started = asyncio.Event()
    owned = []
    async def probe_spawn(*args, **kwargs):
        folder = Path(args[-1]).parent
        process = await real_spawn(sys.executable, "-c", "import time;time.sleep(30)", **kwargs)
        owned.append((process, folder))
        started.set()
        return process
    monkeypatch.setattr(ffmpeg.asyncio, "create_subprocess_exec", probe_spawn)
    if kind == "short":
        coroutine = audio_codec.transcode_for_asr(filename="recording.mp3", content=b"audio")
    else:
        coroutine = visit_import.audio_text("recording.mp3", b"audio", None)
    task = asyncio.create_task(coroutine)
    try:
        await asyncio.wait_for(started.wait(), 3)
        assert owned[0][1].exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert_reaped(owned[0][0].pid)
        assert not owned[0][1].exists()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("kind", ["short", "long"])
async def test_success_and_asr_cancel_keep_workdir_lifetime_correct(monkeypatch, kind):
    import wave
    folders = []
    async def generate_wav(*arguments, timeout):
        output = Path(arguments[-1])
        folders.append(output.parent)
        if "%03d" in output.name:
            output = output.with_name("part-000.wav")
        with wave.open(str(output), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(b"\x00\x00" * 160)
        return ffmpeg.ProcessResult(0, b"")
    monkeypatch.setattr(audio_codec, "run_ffmpeg", generate_wav)
    monkeypatch.setattr(visit_import, "run_ffmpeg", generate_wav)
    if kind == "short":
        name, content, mime = await audio_codec.transcode_for_asr(filename="x.mp3", content=b"audio")
        assert name == "recording.wav" and content.startswith(b"RIFF") and mime == "audio/wav"
    else:
        started = asyncio.Event()
        async def transcribe(**kwargs):
            assert kwargs["content"].startswith(b"RIFF")
            started.set()
            await asyncio.Future()
        task = asyncio.create_task(visit_import.audio_text("x.mp3", b"audio", SimpleNamespace(transcribe=transcribe)))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert folders and all(not folder.exists() for folder in folders)
