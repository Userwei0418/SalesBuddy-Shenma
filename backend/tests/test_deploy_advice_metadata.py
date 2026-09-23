import importlib.util
import json
from pathlib import Path

import pytest

PUBLICATION = Path(__file__).parents[1] / "agent_platform/business_advice/publication.json"


def load():
    path = Path(__file__).parents[1] / "deploy/prepare_advice_runtime.py"
    spec = importlib.util.spec_from_file_location("prepare_advice_runtime", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def binding(agent="old-agent", snapshot="00000000-0000-0000-0000-000000000002"):
    return {"enabled": True, "execution_mode": "filtered_facts", "agent_id": agent,
            "expected_snapshot_id": snapshot}


def test_advice_activation_preserves_other_workspaces_and_existing_capabilities():
    publication = json.loads(PUBLICATION.read_text())
    bindings = {"company": {"visit_entry": binding()}, "other": {"enabled": False}}
    pilot = {"company": {"enabled": False, "capabilities": {"visit_entry": {"enabled": True, "rollout": "production"}}}}
    original = json.dumps([bindings, pilot])
    new_bindings, new_pilot = load().merge_metadata(bindings, pilot, "company", publication)
    assert json.dumps([bindings, pilot]) == original
    assert new_bindings["other"] == bindings["other"]
    assert new_bindings["company"]["visit_entry"] == bindings["company"]["visit_entry"]
    assert new_pilot["company"]["enabled"] is False  # ChatBI remains off.
    for cap, value in publication.items():
        assert new_bindings["company"][cap]["expected_snapshot_id"] == value["snapshot_id"]
        assert new_pilot["company"]["capabilities"][cap] == {"enabled": True, "rollout": "production"}
    assert new_pilot["company"]["capabilities"]["visit_entry"] == pilot["company"]["capabilities"]["visit_entry"]


def test_advice_activation_rejects_unpublished_or_extra_agents():
    publication = json.loads(PUBLICATION.read_text())
    publication["visit_advice"]["published"] = False
    with pytest.raises(AssertionError):
        load().merge_metadata({}, {}, "company", publication)
    publication.pop("visit_advice")
    with pytest.raises(AssertionError):
        load().merge_metadata({}, {}, "company", publication)


def test_advice_activation_refuses_incomplete_inherited_bindings_before_any_write():
    publication = json.loads(PUBLICATION.read_text())
    pilot = {"company": {"capabilities": {"visit_entry": {"enabled": True, "rollout": "production"}}}}
    with pytest.raises(ValueError, match="company/visit_entry"):
        load().merge_metadata({}, pilot, "company", publication)


def test_enabled_chatbi_and_pilots_are_protected_but_disabled_rollouts_are_not_activated():
    module = load()
    with pytest.raises(ValueError, match="company/chatbi"):
        module.validate_rollout_bindings({}, {"company": {"enabled": True}})
    with pytest.raises(ValueError, match="company/visit_entry"):
        module.validate_rollout_bindings({}, {"company": {"capabilities": {"visit_entry": {"enabled": True}}}})
    module.validate_rollout_bindings({}, {"company": {"enabled": False, "capabilities": {
        "visit_entry": {"enabled": False}}}})


def test_recovery_only_adds_selected_verified_bindings_without_mutating_inputs():
    module = load()
    current = {"company": {"opportunity_advice": binding("new-advice")}, "other": {"chatbi": binding()}}
    recovered = {"company": {"visit_entry": binding(), "today_tasks": binding("old-today")}}
    before = json.dumps([current, recovered], sort_keys=True)
    result = module.restore_missing_bindings(current, recovered, "company", ["visit_entry"])
    assert result["company"]["visit_entry"] == recovered["company"]["visit_entry"]
    assert result["company"]["opportunity_advice"] == current["company"]["opportunity_advice"]
    assert result["other"] == current["other"]
    assert "today_tasks" not in result["company"]
    assert json.dumps([current, recovered], sort_keys=True) == before
    assert module.restore_missing_bindings(result, recovered, "company", ["visit_entry"]) == result


@pytest.mark.parametrize("problem", ["missing", "disabled", "invalid_snapshot", "conflict", "no_selection"])
def test_recovery_rejects_ambiguous_or_invalid_sources(problem):
    module = load()
    current, recovered = {}, {"company": {"visit_entry": binding()}}
    capabilities = ["visit_entry"]
    if problem == "missing":
        recovered = {}
    elif problem == "disabled":
        recovered["company"]["visit_entry"]["enabled"] = False
    elif problem == "invalid_snapshot":
        recovered["company"]["visit_entry"]["expected_snapshot_id"] = "revision-is-not-a-snapshot"
    elif problem == "conflict":
        current = {"company": {"visit_entry": binding("different-agent")}}
    else:
        capabilities = []
    with pytest.raises(ValueError):
        module.restore_missing_bindings(current, recovered, "company", capabilities)
