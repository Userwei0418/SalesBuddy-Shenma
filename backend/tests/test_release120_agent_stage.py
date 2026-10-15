import importlib.util
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

DEPLOY = Path(__file__).parents[1] / "deploy"


def load(monkeypatch):
    monkeypatch.syspath_prepend(str(DEPLOY))
    spec = importlib.util.spec_from_file_location("release120_agent_stage", DEPLOY / "release120_agent_stage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("problem", [None, "wrong_ready", "stale_receipt", "wrong_agent", "wrong_prompt"])
def test_publication_transfer_requires_exact_ready_agent_prompt_and_fresh_get(monkeypatch, tmp_path, problem):
    module = load(monkeypatch)
    created = datetime.now(UTC) - timedelta(minutes=1)
    ready = {"bundle_id": "release-1.2.0-test", "created_at": created.isoformat(), "prompt_sha256": {}}
    for cap in module.CAPABILITIES:
        folder = tmp_path / "publications" / cap
        folder.mkdir(parents=True)
        prompt = cap + " reviewed prompt\n"
        (folder / "system_prompt.txt").write_text(prompt)
        (folder / "agent.json").write_text(json.dumps({"agent_soul": {"prompt": {"system_prompt": prompt}}}))
        ready["prompt_sha256"][cap] = module.sha(folder / "system_prompt.txt")
        raw = {"id": module.AGENT_IDS[cap], "snapshot_id": "00000000-0000-0000-0000-000000000003",
               "workspace_id": "a06dcd4a-af4b-4a91-85e6-109e6e7e5720", "published": True,
               "record_type": "platform_publication_and_get_verification",
               "observed_at": (created + timedelta(seconds=5)).isoformat(),
               "prompt_sha256": ready["prompt_sha256"][cap]}
        if cap == "work_report":
            if problem == "stale_receipt":
                raw["observed_at"] = (created - timedelta(seconds=1)).isoformat()
            elif problem == "wrong_agent":
                raw["id"] = module.AGENT_IDS["finance_assistant"]
            elif problem == "wrong_prompt":
                raw["prompt_sha256"] = "0" * 64
        (folder / "publication.json").write_text(json.dumps(raw))
    ready_path = tmp_path / "READY.json"
    module.write(ready_path, ready)
    module.write(tmp_path / "publications" / "RECEIPT.json", {
        "bundle_id": ready["bundle_id"],
        "ready_sha256": "0" * 64 if problem == "wrong_ready" else module.sha(ready_path),
        "publications_sha256": {cap: module.sha(tmp_path / "publications" / cap / "publication.json")
                                for cap in module.CAPABILITIES}})
    if problem:
        with pytest.raises(module.Refused):
            module.checked_publications(ready, ready_path)
    else:
        _, publications = module.checked_publications(ready, ready_path)
        assert set(publications) == set(module.CAPABILITIES)


@pytest.mark.parametrize("problem", [None, "unrelated_file", "binding_mode", "different_rollout_source"])
def test_configuration_delta_accepts_only_managed_binding_replacement(monkeypatch, problem):
    module = load(monkeypatch)
    managed = str(module.metadata.TARGET)
    before_files = {service: {"properties": {"User": "sales"}, "file_metadata": {
        managed: [1, 20, 100, 0o100640], "/etc/other.env": [2, 30, 101, 0o100600]
    }} for service in module.metadata.SERVICES}
    after_files = deepcopy(before_files)
    after_files[module.metadata.SERVICES[0]]["file_metadata"][managed][:3] = [3, 40, 102]
    if problem == "unrelated_file":
        after_files[module.metadata.SERVICES[0]]["file_metadata"]["/etc/other.env"][2] += 1
    elif problem == "binding_mode":
        after_files[module.metadata.SERVICES[0]]["file_metadata"][managed][3] = 0o100644
    before = {"service_metadata_sha256": module.metadata.digest(before_files),
              "agent_metadata": {"rollout_source_sha256": "same", "bindings_sha256": "before"}}
    after = {"service_metadata_sha256": module.metadata.digest(after_files),
             "agent_metadata": {"rollout_source_sha256": "same", "bindings_sha256": "after"}}
    if problem == "different_rollout_source":
        after["agent_metadata"]["rollout_source_sha256"] = "changed"
    result = {"verified": after["agent_metadata"]}
    if problem:
        with pytest.raises(module.Refused):
            module.allowed_configuration_delta(before, after, result, before_files, after_files)
    else:
        module.allowed_configuration_delta(before, after, result, before_files, after_files)


def test_stage_cannot_borrow_lock_outside_migrated_agent_phase(monkeypatch, tmp_path):
    module = load(monkeypatch)
    journal = tmp_path / "TRANSACTION.json"
    journal.write_text(json.dumps({"id": "bundle", "phase": "stopped", "migration_attempted": False}))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    with pytest.raises(module.Refused, match="outside"):
        module.held_window({"journal": str(journal), "bundle_id": "bundle"}, lambda _: "inactive")


def test_large_caller_journal_has_separate_one_mib_bound(monkeypatch, tmp_path):
    module = load(monkeypatch)
    path = tmp_path / "TRANSACTION.json"
    document = {"id": "bundle", "database_receipt": "x" * 263275}
    path.write_text(json.dumps(document))
    assert module.read_journal(path) == document
    with pytest.raises(ValueError, match="Metadata exceeds"):
        module.read(path)
    path.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(module.Refused, match="exceeds 1 MiB"):
        module.read_journal(path)
