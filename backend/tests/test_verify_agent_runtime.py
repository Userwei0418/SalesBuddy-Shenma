import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

DEPLOY = Path(__file__).parents[1] / "deploy"


def load(monkeypatch):
    monkeypatch.syspath_prepend(str(DEPLOY))
    spec = importlib.util.spec_from_file_location("verify_agent_runtime", DEPLOY / "verify_agent_runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def settings(tmp_path):
    item = {"enabled": True, "agent_id": "published-agent", "execution_mode": "filtered_facts",
            "expected_snapshot_id": "00000000-0000-0000-0000-000000000002"}
    pilot = {"company": {"enabled": False, "capabilities": {"visit_entry": {"enabled": True, "rollout": "production"}}}}
    pilot_path = tmp_path / "rollout.json"
    pilot_path.write_text(json.dumps(pilot))
    return SimpleNamespace(agent_platform_bindings_json=json.dumps({"company": {"visit_entry": item}}),
                           agent_fde_pilot_path=str(pilot_path), agent_fde_pilot_json="{}",
                           agent_fde_visit_entry_api_key="SECRET_MUST_NOT_APPEAR")


def test_effective_metadata_digest_is_canonical_and_does_not_expose_adapter_credentials(monkeypatch, tmp_path):
    module = load(monkeypatch)
    worker = settings(tmp_path)
    api = SimpleNamespace(**vars(worker))
    del api.agent_fde_visit_entry_api_key  # Only the Worker requires this adapter.
    api.agent_platform_bindings_json = json.dumps(json.loads(worker.agent_platform_bindings_json), indent=4)
    expected = module.verify(worker)
    assert module.verify(api) == expected
    assert set(expected) == {"schema_version", "bindings_sha256", "rollout_sha256", "rollout_source_sha256"}
    assert all(len(value) == 64 for key, value in expected.items() if key.endswith("sha256"))
    assert "SECRET" not in json.dumps(expected)
    assert "published-agent" not in json.dumps(expected)


@pytest.mark.parametrize("problem", ["missing_binding", "invalid_snapshot", "enabled_chatbi",
                                    "wrong_root", "wrong_workspace", "wrong_capabilities", "wrong_capability",
                                    "invalid_json", "too_large", "missing_file", "relative_path"])
def test_invalid_effective_metadata_blocks_preflight(monkeypatch, tmp_path, problem):
    module = load(monkeypatch)
    current = settings(tmp_path)
    pilot_path = Path(current.agent_fde_pilot_path)
    pilot = json.loads(pilot_path.read_text())
    if problem == "missing_binding":
        current.agent_platform_bindings_json = "{}"
    elif problem == "invalid_snapshot":
        bindings = json.loads(current.agent_platform_bindings_json)
        bindings["company"]["visit_entry"]["expected_snapshot_id"] = "unpublished"
        current.agent_platform_bindings_json = json.dumps(bindings)
    elif problem == "enabled_chatbi":
        pilot["company"]["enabled"] = True
    elif problem == "wrong_root":
        pilot = []
    elif problem == "wrong_workspace":
        pilot["company"] = None
    elif problem == "wrong_capabilities":
        pilot["company"]["capabilities"] = []
    elif problem == "wrong_capability":
        pilot["company"]["capabilities"]["visit_entry"] = True
    elif problem == "invalid_json":
        current.agent_platform_bindings_json = "SECRET_INVALID_JSON"
    elif problem == "too_large":
        current.agent_platform_bindings_json = " " * 64001
    elif problem == "missing_file":
        current.agent_fde_pilot_path = str(tmp_path / "absent.json")
    else:
        current.agent_fde_pilot_path = "relative.json"
    pilot_path.write_text(json.dumps(pilot))
    with pytest.raises((OSError, ValueError)):
        module.verify(current)


def test_disabled_capability_remains_disabled_and_source_changes_are_detectable(monkeypatch, tmp_path):
    module = load(monkeypatch)
    current = settings(tmp_path)
    current.agent_platform_bindings_json = "{}"
    pilot = {"company": {"enabled": False, "capabilities": {"visit_entry": {"enabled": False}}}}
    Path(current.agent_fde_pilot_path).write_text(json.dumps(pilot))
    before = module.verify(current)
    current.agent_fde_pilot_json = json.dumps(pilot)
    current.agent_fde_pilot_path = ""
    inline = module.verify(current)
    assert inline["rollout_sha256"] == before["rollout_sha256"]
    assert inline["rollout_source_sha256"] != before["rollout_source_sha256"]
    assert json.loads(current.agent_fde_pilot_json)["company"]["enabled"] is False


def test_cli_failure_does_not_echo_invalid_metadata_or_settings(monkeypatch, tmp_path, capsys):
    module = load(monkeypatch)
    current = settings(tmp_path)
    current.agent_platform_bindings_json = "SECRET_INVALID_JSON"
    monkeypatch.setattr(module, "get_settings", lambda: current)
    with pytest.raises(SystemExit) as failure:
        module.main()
    assert "SECRET" not in str(failure.value)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("mode", ["same", "different", "invalid"])
def test_forward_service_pair_checks_reject_drift_or_failed_verification(tmp_path, mode):
    # Exercise the actual deploy function with a stand-in launcher executable;
    # no systemd commands, credentials or production files are involved.
    script = (DEPLOY / "deploy-forward-release.sh").read_text()
    start = script.index("verify_agent_runtime() {")
    end = script.index("\n}", start) + 2
    function = script[start:end]
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    worker_digest = "api" if mode == "same" else "worker"
    worker_action = ("exit 3" if mode == "invalid" else "echo '{\"digest\":\"" + worker_digest + "\"}'")
    python.write_text("#!/bin/bash\nset -eu\n"
                      "case \"$*\" in\n"
                      "  *sales-api.service*) echo '{\"digest\":\"api\"}';;\n"
                      + "  *sales-worker.service*) " + worker_action + ";;\n"
                      + "esac\n")
    python.chmod(0o700)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    result = subprocess.run(  # noqa: S603 — checked-in function and isolated test fixture only.
        ["/bin/bash", "-c", 'set -euo pipefail\nNEW="$1"; EVIDENCE="$2"; COMMIT=0123456789abcdef\n'
         + function + '\nverify_agent_runtime before\n', "verify-test", str(tmp_path), str(evidence)],
        capture_output=True, text=True,
    )
    assert (result.returncode == 0) == (mode == "same")


def test_forward_checks_surround_downtime_and_success():
    script = (DEPLOY / "deploy-forward-release.sh").read_text()
    assert script.index("verify_agent_runtime before") < script.index("STOPPED=1")
    assert script.index("health \"$NEW\"") < script.index("verify_agent_runtime after")
    assert script.index("verify_agent_runtime after") < script.index("FORWARD_RELEASE_READY=")
    assert 'cmp "$EVIDENCE/$service.agent-runtime.before.json" "$EVIDENCE/$service.agent-runtime.after.json"' in script
