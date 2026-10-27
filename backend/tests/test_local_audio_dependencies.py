"""LOCAL validates a managed dependency before touching its running environment."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("local_dev_audio_test", ROOT / "tools/local/dev.py")
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)
import audio_dependencies as dependencies  # noqa: E402


def executable(tmp_path):
    path = tmp_path / "managed directory" / "ffmpeg"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\nprintf 'ffmpeg version test-fixture\\n'\n")
    path.chmod(0o700)
    return str(path)


def test_explicit_path_probe_and_service_injection_ignore_shell_value(tmp_path):
    path = executable(tmp_path)
    env = {"FFMPEG_EXECUTABLE": "/bin/sh", "UNCHANGED": "value"}
    dependencies.apply_audio_config({"ffmpeg_executable": path}, env)
    assert env == {"FFMPEG_EXECUTABLE": path, "UNCHANGED": "value"}


def test_known_path_discovery_does_not_use_path_or_invalid_explicit_fallback(monkeypatch, tmp_path):
    path = executable(tmp_path)
    monkeypatch.setattr(dependencies, "CANDIDATES", (path,))
    monkeypatch.setenv("PATH", "/fake/injected")
    assert dependencies.preflight_audio({}) == path
    for invalid in ("ffmpeg", "", "/missing/ffmpeg", None):
        with pytest.raises(RuntimeError, match="--ffmpeg-path"):
            dependencies.preflight_audio({"ffmpeg_executable": invalid})


def test_wrong_binary_and_timeout_fail_before_services_are_stopped(monkeypatch, tmp_path):
    path = executable(tmp_path)
    Path(path).write_text("#!/bin/sh\nprintf 'not ffmpeg\\n'\n")
    with pytest.raises(RuntimeError, match="LOCAL audio dependency"):
        dependencies.preflight_audio({"ffmpeg_executable": path})
    monkeypatch.setattr(dev, "source_files", lambda: {})
    monkeypatch.setattr(dev, "config", lambda **kwargs: {"ffmpeg_executable": path})
    monkeypatch.setattr(dev, "preflight_model", lambda cfg: None)
    calls = []
    monkeypatch.setattr(dev, "stop_apps", lambda: calls.append("stopped"))
    with pytest.raises(RuntimeError, match="LOCAL audio dependency"):
        dev.up()
    assert calls == []
    def timeout(*args, **kwargs):
        raise dependencies.subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(dependencies.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="LOCAL audio dependency"):
        dependencies.preflight_audio({"ffmpeg_executable": path})


def test_configure_only_persists_validated_path_without_stopping_services(monkeypatch, tmp_path):
    path = executable(tmp_path)
    state = tmp_path / "state"
    monkeypatch.setattr(dev, "LOCAL", state)
    dev.configure_ffmpeg({"kind": "salesbuddy-local-v1", "existing": "unchanged"}, path)
    import json
    assert json.loads((state / "settings.json").read_text()) == {
        "kind": "salesbuddy-local-v1", "existing": "unchanged", "ffmpeg_executable": path,
    }
    assert (state / "settings.json").stat().st_mode & 0o777 == 0o600
    before = (state / "settings.json").read_bytes()
    with pytest.raises(RuntimeError):
        dev.configure_ffmpeg({}, "/not-here/ffmpeg")
    assert (state / "settings.json").read_bytes() == before


def test_snapshot_contains_dependency_module_and_runtime_loader_uses_it():
    assert dev.selected("tools/local/audio_dependencies.py")
    assert dev.selected("backend/src/sales_backend/integrations/ffmpeg_config.py")
