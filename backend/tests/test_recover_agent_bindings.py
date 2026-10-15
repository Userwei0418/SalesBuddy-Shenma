import importlib.util
from pathlib import Path

import pytest


def load(monkeypatch):
    path = Path(__file__).parents[1] / "deploy/recover_agent_bindings.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("recover_agent_bindings", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(module):
    item = {"enabled": True, "agent_id": "agent", "execution_mode": "filtered_facts",
            "expected_snapshot_id": "00000000-0000-0000-0000-000000000002"}
    source = {"company": {"visit_entry": item}}
    current = {"company": {"customer_advice": dict(item, agent_id="advice")}}
    proposed = {"company": {**current["company"], **source["company"]}}
    plan = {"schema_version": 1, "workspace_id": "company", "source_sha256": module.digest(source),
            "current_sha256": module.digest(current), "proposed_sha256": module.digest(proposed),
            "proposed_bindings": proposed, "changes": [{"capability": "visit_entry"}]}
    pilot = {"company": {"enabled": False, "capabilities": {"visit_entry": {"enabled": True}}}}
    return current, source, pilot, plan


def test_reviewed_recovery_is_additive_and_replay_is_noop(monkeypatch):
    module = load(monkeypatch)
    current, source, pilot, plan = fixture(module)
    proposed, unchanged = module.verify_plan(current, source, pilot, plan)
    assert not unchanged
    assert proposed["company"]["customer_advice"] == current["company"]["customer_advice"]
    assert module.verify_plan(proposed, source, pilot, plan) == (proposed, True)
    assert pilot["company"]["enabled"] is False


@pytest.mark.parametrize("change", ["source", "current", "proposed", "duplicate", "rollout"])
def test_recovery_rejects_stale_or_incomplete_plan(monkeypatch, change):
    module = load(monkeypatch)
    current, source, pilot, plan = fixture(module)
    if change == "source":
        source["company"]["visit_entry"]["agent_id"] = "unexpected"
    elif change == "current":
        current["unreviewed"] = {}
    elif change == "proposed":
        plan["proposed_sha256"] = "wrong"
    elif change == "duplicate":
        plan["changes"] *= 2
    else:
        pilot["company"]["capabilities"]["today_tasks"] = {"enabled": True}
    with pytest.raises(ValueError):
        module.verify_plan(current, source, pilot, plan)
