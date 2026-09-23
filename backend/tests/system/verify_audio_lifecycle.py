"""Bounded real-ffmpeg probe: owned temp files/processes only, no DB or model API.

Set --overlay to a directory containing just the candidate sales_backend modules
when using an existing immutable release's Python runtime. Never change that release.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import wave


def candidate_modules(overlay):
    import sales_backend
    import sales_backend.integrations
    import sales_backend.services
    if overlay:
        for package, relative in ((sales_backend, "sales_backend"),
                                  (sales_backend.integrations, "sales_backend/integrations"),
                                  (sales_backend.services, "sales_backend/services")):
            package.__path__ = [str(Path(overlay) / relative), *package.__path__]
    from sales_backend.integrations import audio_codec, ffmpeg
    from sales_backend.services import visit_import
    if overlay:
        assert all(Path(module.__file__).is_relative_to(Path(overlay))
                   for module in (audio_codec, ffmpeg, visit_import))
    return audio_codec, ffmpeg, visit_import


async def main(overlay=None):
    audio_codec, ffmpeg, visit_import = candidate_modules(overlay)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)
    content = stream.getvalue()
    results = []
    with TemporaryDirectory(prefix="salegent-audio-probe-") as root:
        folders = []
        def working_directory(*, prefix):
            directory = TemporaryDirectory(prefix=prefix, dir=root)
            folders.append(Path(directory.name))
            return directory
        audio_codec.TemporaryDirectory = visit_import.TemporaryDirectory = working_directory
        filename, wav, mime = await audio_codec.transcode_for_asr(filename="probe.wav", content=content)
        assert filename == "recording.wav" and mime == "audio/wav" and wav.startswith(b"RIFF")
        results.append("short_wav_success")
        async def transcribe(**kwargs):
            assert kwargs["content"].startswith(b"RIFF")
            return SimpleNamespace(text="isolated probe", trace_id="no-model-call")
        text, evidence = await visit_import.audio_text("probe.wav", content, SimpleNamespace(transcribe=transcribe))
        assert text == "isolated probe" and len(evidence) == 1
        assert folders and all(not folder.exists() for folder in folders)
        results.append("long_wav_success_without_model_call")
        real_spawn = asyncio.create_subprocess_exec
        for mode in ("cancel", "timeout", "cancel_during_spawn"):
            started, release = asyncio.Event(), asyncio.Event()
            processes = []
            async def tracked_spawn(*args, **kwargs):
                process = await real_spawn(*args, **kwargs)
                processes.append(process)
                started.set()
                if mode == "cancel_during_spawn":
                    await release.wait()
                return process
            ffmpeg.asyncio.create_subprocess_exec = tracked_spawn
            task = asyncio.create_task(ffmpeg.run_ffmpeg(
                "-nostdin", "-v", "error", "-y", "-re", "-f", "lavfi", "-i",
                "sine=frequency=1000:sample_rate=16000", "-t", "30", str(Path(root) / "probe.wav"),
                timeout=0.15 if mode == "timeout" else 10,
            ))
            try:
                await asyncio.wait_for(started.wait(), 3)
                if mode != "timeout":
                    task.cancel()
                    await asyncio.sleep(0)
                    release.set()
                try:
                    await asyncio.wait_for(task, 5)
                except (asyncio.CancelledError, TimeoutError):
                    pass
                else:
                    raise AssertionError("expected cancellation or timeout")
                assert processes and all(process.returncode is not None for process in processes)
                for process in processes:
                    try:
                        os.kill(process.pid, 0)
                    except ProcessLookupError:
                        pass
                    else:
                        raise AssertionError("ffmpeg was not reaped")
                results.append("real_ffmpeg_" + mode + "_reaped")
            finally:
                release.set()
                ffmpeg.asyncio.create_subprocess_exec = real_spawn
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    print({"passed": results, "model_calls": 0, "database_calls": 0})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--overlay")
    args = parser.parse_args()
    asyncio.run(main(args.overlay))
