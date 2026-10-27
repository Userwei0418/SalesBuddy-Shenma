import hashlib
import importlib.util
import json
import shlex
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

DEPLOY = Path(__file__).parents[1] / "deploy"
WORKSPACE = "00000000-0000-0000-0000-000000000001"
USER = "00000000-0000-0000-0000-000000000002"


def load(monkeypatch):
    monkeypatch.syspath_prepend(str(DEPLOY))
    spec = importlib.util.spec_from_file_location("prepare_agent_metadata", DEPLOY / "prepare_agent_metadata.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def published_templates(module, tmp_path):
    # Synthetic receipts isolate deployment mechanics from pending remote
    # publication. Never change the actual templates' historical receipts.
    directory = tmp_path / "published-templates"
    for cap in module.SUPPORTED_CAPABILITIES:
        source = module.PUBLICATIONS / cap
        if source.is_dir():
            shutil.copytree(source, directory / cap)
    for cap in module.TEMPLATE_VERIFIED_CAPABILITIES:
        publication = directory / cap / "publication.json"
        raw = json.loads(publication.read_text())
        raw["prompt_sha256"] = hashlib.sha256((directory / cap / "system_prompt.txt").read_bytes()).hexdigest()
        publication.write_text(json.dumps(raw))
    module.PUBLICATIONS = directory
    return directory


def fixture(module, tmp_path):
    published_templates(module, tmp_path)
    publications = module.publication_metadata(module.PUBLICATIONS)
    report = publications["operating_report"]
    binding = {
        "enabled": True,
        "execution_mode": "filtered_facts",
        "agent_id": report["id"],
        "expected_snapshot_id": report.get("previous_binding_snapshot_id", report["previous_snapshot_id"]),
        "model": report["model"],
    }
    bindings = {WORKSPACE: {"operating_report": binding, "visit_entry": dict(binding)}, "another": {}}
    pilot = {
        WORKSPACE: {
            "enabled": False,
            "capabilities": {
                "operating_report": {"enabled": True, "rollout": "production"},
                "visit_entry": {
                    "enabled": True,
                    "rollout": "pilot",
                    "user_ids": [USER],
                    "expires_at": "2099-01-01T00:00:00+00:00",
                },
            },
        },
        "another": {"enabled": False},
    }
    pilot_path, target = tmp_path / "pilot.json", tmp_path / "bindings.env"
    pilot_path.write_text(json.dumps(pilot))
    target.write_text(
        "AGENT_PLATFORM_BINDINGS_JSON=" + shlex.quote(json.dumps(bindings)) + "\n"
        "AGENT_FDE_PILOT_PATH=" + shlex.quote(str(pilot_path)) + "\n"
    )
    settings = SimpleNamespace(
        agent_platform_bindings_json=json.dumps(bindings),
        agent_fde_pilot_path=str(pilot_path),
        agent_fde_pilot_json="{}",
    )
    for cap in module.CAPABILITIES:
        setattr(settings, "agent_fde_" + cap + "_id", publications[cap]["id"])
        setattr(settings, "agent_fde_" + cap + "_api_key", "SECRET_NEVER_SERIALIZE")
    request = module.rollout_request("pilot", [USER], "2099-01-01T00:00:00Z")
    return settings, target, pilot_path, request


def test_dry_plan_preserves_report_identity_model_rollout_and_unrelated_metadata(monkeypatch, tmp_path):
    module = load(monkeypatch)
    settings, target, pilot_path, request = fixture(module, tmp_path)
    originals = target.read_bytes(), pilot_path.read_bytes()
    plan = module.build_plan(settings, WORKSPACE, request, target=target)
    assert originals == (target.read_bytes(), pilot_path.read_bytes())
    assert "SECRET" not in json.dumps(plan)
    before, after = plan["before"], plan["after"]
    old = before["bindings"][WORKSPACE]["operating_report"]
    new = after["bindings"][WORKSPACE]["operating_report"]
    assert new == {**old, "expected_snapshot_id": plan["publications"]["operating_report"]["snapshot_id"]}
    assert after["pilot"][WORKSPACE]["enabled"] is False
    assert (
        after["pilot"][WORKSPACE]["capabilities"]["operating_report"]
        == before["pilot"][WORKSPACE]["capabilities"]["operating_report"]
    )
    assert after["bindings"][WORKSPACE]["visit_entry"] == before["bindings"][WORKSPACE]["visit_entry"]
    assert after["bindings"]["another"] == before["bindings"]["another"]
    assert after["pilot"][WORKSPACE]["capabilities"]["competency_review"] == request
    assert request["rollout"] == "pilot"


def test_explicit_binding_predecessor_is_one_cas_value_not_an_expanded_allowlist(monkeypatch, tmp_path):
    module = load(monkeypatch)
    settings, _, pilot_path, request = fixture(module, tmp_path)
    publications = module.publication_metadata(module.PUBLICATIONS)
    report = publications["operating_report"]
    # Publication history changes independently of this regression. Exercise a
    # measured older binding explicitly rather than assuming current metadata.
    report["previous_snapshot_id"] = "00000000-0000-0000-0000-000000000003"
    assert report["previous_binding_snapshot_id"] != report["previous_snapshot_id"]
    bindings = json.loads(settings.agent_platform_bindings_json)
    pilot = json.loads(pilot_path.read_text())
    # The measured, older runtime binding is allowed only through its explicit CAS.
    module.proposed_metadata(bindings, pilot, WORKSPACE, publications, request)
    platform_only = json.loads(json.dumps(publications))
    platform_only["operating_report"].pop("previous_binding_snapshot_id")
    with pytest.raises(ValueError):
        module.proposed_metadata(bindings, pilot, WORKSPACE, platform_only, request)
    for unreviewed in (report["previous_snapshot_id"], USER):
        bindings[WORKSPACE]["operating_report"]["expected_snapshot_id"] = unreviewed
        with pytest.raises(ValueError):
            module.proposed_metadata(bindings, pilot, WORKSPACE, publications, request)
    bindings[WORKSPACE]["operating_report"]["expected_snapshot_id"] = report["snapshot_id"]
    module.proposed_metadata(bindings, pilot, WORKSPACE, publications, request)


def test_worker_only_adapters_keep_a_common_metadata_plan_without_giving_keys_to_api(monkeypatch, tmp_path):
    module = load(monkeypatch)
    worker, target, _, request = fixture(module, tmp_path)
    api = SimpleNamespace(**vars(worker))
    for cap in module.CAPABILITIES:
        setattr(api, "agent_fde_" + cap + "_id", "")
        setattr(api, "agent_fde_" + cap + "_api_key", "")
    api_plan = module.build_plan(api, WORKSPACE, request, target=target)
    worker_plan = module.build_plan(worker, WORKSPACE, request, target=target)
    assert api_plan == worker_plan
    observed = module.verify_services(
        api_plan["before"]["runtime"],
        api_plan["publications"],
        reader=lambda service: module.inspect(api if service == module.SERVICES[0] else worker),
    )
    assert all(not item["key_present"] for item in observed[module.SERVICES[0]].values())
    assert all(item["key_present"] for item in observed[module.SERVICES[1]].values())
    assert "SECRET" not in json.dumps(observed)


@pytest.mark.parametrize("cap", ["operating_report", "competency_review"])
@pytest.mark.parametrize("problem", ["missing_key", "missing_id", "wrong_id"])
def test_worker_requires_every_executed_adapter_configuration(monkeypatch, tmp_path, cap, problem):
    module = load(monkeypatch)
    api, target, _, request = fixture(module, tmp_path)
    plan = module.build_plan(api, WORKSPACE, request, target=target)
    worker = SimpleNamespace(**vars(api))
    if problem == "missing_key":
        setattr(worker, "agent_fde_" + cap + "_api_key", "")
    else:
        setattr(worker, "agent_fde_" + cap + "_id", "" if problem == "missing_id" else USER)
    with pytest.raises(ValueError):
        module.verify_services(
            plan["before"]["runtime"],
            plan["publications"],
            reader=lambda service: module.inspect(api if service == module.SERVICES[0] else worker),
        )


@pytest.mark.parametrize("identifier", ["", USER])
def test_optional_api_adapter_cannot_have_a_mismatched_id_or_key_without_id(monkeypatch, tmp_path, identifier):
    module = load(monkeypatch)
    worker, target, _, request = fixture(module, tmp_path)
    plan = module.build_plan(worker, WORKSPACE, request, target=target)
    api = SimpleNamespace(**vars(worker))
    api.agent_fde_operating_report_id = identifier
    with pytest.raises(ValueError):
        module.verify_services(
            plan["before"]["runtime"],
            plan["publications"],
            reader=lambda service: module.inspect(api if service == module.SERVICES[0] else worker),
        )


@pytest.mark.parametrize(
    "problem", [None, "api_missing_key", "api_missing_id", "api_wrong_id", "worker_wrong_id", "worker_key_without_id"]
)
@pytest.mark.parametrize("api_cap", ["task_import", "finance_assistant"])
@pytest.mark.parametrize("worker_cap", ["operating_report", "work_report", "competency_review"])
def test_execution_service_requires_adapter_and_peer_may_omit_credentials(monkeypatch, problem, api_cap, worker_cap):
    module = load(monkeypatch)
    publications = {api_cap: {"id": WORKSPACE}, worker_cap: {"id": USER}}
    runtime = {"synthetic": True}
    snapshots = {
        "sales-api.service": {"runtime": runtime, "credentials": {
            api_cap: {"agent_id": WORKSPACE, "key_present": True},
            worker_cap: {"agent_id": "", "key_present": False},
        }},
        "sales-worker.service": {"runtime": runtime, "credentials": {
            api_cap: {"agent_id": "", "key_present": False},
            worker_cap: {"agent_id": USER, "key_present": True},
        }},
    }
    if problem:
        service = "sales-api.service" if problem.startswith("api_") else "sales-worker.service"
        adapter = snapshots[service]["credentials"][api_cap]
        if problem.endswith("missing_key"):
            adapter["key_present"] = False
        elif problem.endswith("missing_id"):
            adapter["agent_id"] = ""
        elif problem.endswith("wrong_id"):
            adapter["agent_id"] = USER
        else:
            adapter["key_present"] = True
        with pytest.raises(ValueError):
            module.verify_services(runtime, publications, reader=snapshots.__getitem__)
    else:
        observed = module.verify_services(runtime, publications, reader=snapshots.__getitem__)
        assert observed["sales-api.service"][api_cap]["key_present"]
        assert not observed["sales-worker.service"][api_cap]["key_present"]


@pytest.mark.parametrize(
    "mode,users,expiry",
    [
        ("pilot", [], "2099-01-01T00:00:00Z"),
        ("pilot", [USER], "2020-01-01T00:00:00Z"),
        ("pilot", [USER], "2099-01-01T00:00:00"),
        ("pilot", ["not-a-uuid"], "2099-01-01T00:00:00Z"),
        ("production", [USER], None),
    ],
)
def test_rollout_requires_explicit_bounded_or_explicit_production_choice(monkeypatch, mode, users, expiry):
    module = load(monkeypatch)
    with pytest.raises(ValueError):
        module.rollout_request(mode, users, expiry)
    assert module.rollout_request("production", [], None) == {"enabled": True, "rollout": "production"}


@pytest.mark.parametrize(
    "problem",
    ["other_agent", "unreviewed_snapshot", "existing_competency", "existing_rollout", "chatbi", "secret_field"],
)
def test_plan_refuses_unreviewed_or_unsafe_changes(monkeypatch, tmp_path, problem):
    module = load(monkeypatch)
    settings, target, pilot_path, request = fixture(module, tmp_path)
    bindings, pilot = json.loads(settings.agent_platform_bindings_json), json.loads(pilot_path.read_text())
    if problem == "other_agent":
        bindings[WORKSPACE]["operating_report"]["agent_id"] = "different"
    elif problem == "unreviewed_snapshot":
        bindings[WORKSPACE]["operating_report"]["expected_snapshot_id"] = USER
    elif problem == "existing_competency":
        bindings[WORKSPACE]["competency_review"] = dict(bindings[WORKSPACE]["operating_report"])
    elif problem == "existing_rollout":
        pilot[WORKSPACE]["capabilities"]["competency_review"] = {"enabled": False}
    elif problem == "chatbi":
        pilot[WORKSPACE]["enabled"] = True
    else:
        bindings[WORKSPACE]["operating_report"]["api_key"] = "SECRET_NEVER_SERIALIZE"
    with pytest.raises(ValueError):
        module.nonsecret_metadata(bindings, pilot)
        module.proposed_metadata(bindings, pilot, WORKSPACE, module.publication_metadata(module.PUBLICATIONS), request)


@pytest.mark.parametrize(
    "change", ["target", "pilot", "missing_key", "fresh_worker_drift", "plan_tamper", "same_service"]
)
def test_apply_plan_checks_sha_cas_and_fresh_pair_before_writes(monkeypatch, tmp_path, change):
    module = load(monkeypatch)
    settings, target, pilot_path, request = fixture(module, tmp_path)
    if change == "missing_key":
        settings.agent_fde_competency_review_api_key = ""
    plan = module.build_plan(settings, WORKSPACE, request, target=target)
    sha = module.digest(plan)
    api = {"service": module.SERVICES[0], "plan_sha256": sha, "plan": plan}
    worker = json.loads(json.dumps({**api, "service": module.SERVICES[1]}))
    if change == "target":
        target.write_text(target.read_text() + "\n")
    elif change == "pilot":
        pilot_path.write_text(pilot_path.read_text() + "\n")
    elif change == "plan_tamper":
        worker["plan"]["after"]["bindings"] = {}
    elif change == "same_service":
        worker["service"] = api["service"]
    expected = module.inspect(settings)

    def reader(service):
        return {} if change == "fresh_worker_drift" and service == module.SERVICES[1] else expected

    originals = target.read_bytes(), pilot_path.read_bytes()
    with pytest.raises(ValueError):
        checked = module.checked_plan(api, worker, sha)
        module.apply_plan(settings, checked, tmp_path / "evidence", target=target, reader=reader)
    assert originals == (target.read_bytes(), pilot_path.read_bytes())
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("failure", [None, "second_write", "postverify", "adapter_drift"])
def test_atomic_pair_apply_or_rollback_without_credentials(monkeypatch, tmp_path, failure):
    module = load(monkeypatch)
    settings, target, pilot_path, request = fixture(module, tmp_path)
    plan = module.build_plan(settings, WORKSPACE, request, target=target)
    originals = target.read_bytes(), pilot_path.read_bytes()
    evidence = tmp_path / "evidence"
    calls = []

    def atomic(path, text, *_):
        calls.append(path)
        if failure == "second_write" and path == pilot_path and calls.count(pilot_path) == 1:
            raise OSError("simulated disk error")
        path.write_text(text)

    def reader(service):
        # Re-read the generated metadata just as each fresh systemd probe does.
        copied = SimpleNamespace(**vars(settings))
        values = dict(shlex.split(line)[0].split("=", 1) for line in target.read_text().splitlines())
        copied.agent_platform_bindings_json = values["AGENT_PLATFORM_BINDINGS_JSON"]
        if failure == "postverify" and target in calls and service == module.SERVICES[1]:
            return {}
        if failure == "adapter_drift" and target in calls and service == module.SERVICES[0]:
            copied.agent_fde_operating_report_id = ""
            copied.agent_fde_operating_report_api_key = ""
        return module.inspect(copied)

    monkeypatch.setattr(module, "atomic_write", atomic)
    if failure:
        with pytest.raises((ValueError, OSError)):
            module.apply_plan(settings, plan, evidence, target=target, reader=reader)
        assert originals == (target.read_bytes(), pilot_path.read_bytes())
        assert not (evidence / "result.json").exists()
    else:
        module.apply_plan(settings, plan, evidence, target=target, reader=reader)
        assert json.loads(pilot_path.read_text()) == plan["after"]["pilot"]
        assert json.loads((evidence / "result.json").read_text())["applied"] is True
    assert "SECRET" not in "".join(path.read_text() for path in evidence.iterdir())


def test_guard_refuses_active_service_or_competing_lock(monkeypatch, tmp_path):
    module = load(monkeypatch)
    lock = tmp_path / ".deploy.lock"
    monkeypatch.setattr(module, "LOCK", lock)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout="active"))
    with pytest.raises(ValueError):
        with module.deployment_guard():
            pytest.fail("An active service must stop the operation")
    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(stdout="inactive"))
    with lock.open("a") as held:
        module.fcntl.flock(held, module.fcntl.LOCK_EX | module.fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            with module.deployment_guard():
                pytest.fail("A second lock holder must not enter")


def subset_fixture(module, tmp_path):
    settings, target, pilot_path, _ = fixture(module, tmp_path)
    selected = ("operating_report", "opportunity_draft")
    publications = module.publication_metadata(module.PUBLICATIONS, ("operating_report",))
    publications["opportunity_draft"] = {
        "id": "00000000-0000-0000-0000-000000000004",
        "previous_snapshot_id": "00000000-0000-0000-0000-000000000005",
        "previous_binding_snapshot_id": "00000000-0000-0000-0000-000000000006",
        "snapshot_id": "00000000-0000-0000-0000-000000000007",
        "model": "published-model",
    }
    directory = tmp_path / "publications"
    for cap, publication in publications.items():
        folder = directory / cap
        folder.mkdir(parents=True)
        (folder / "publication.json").write_text(json.dumps({**publication, "published": True}))
    bindings = json.loads(settings.agent_platform_bindings_json)
    opportunity = publications["opportunity_draft"]
    bindings[WORKSPACE]["opportunity_draft"] = {
        "enabled": True,
        "execution_mode": "filtered_facts",
        "agent_id": opportunity["id"],
        "expected_snapshot_id": opportunity["previous_binding_snapshot_id"],
        "model": "keep-runtime-model",
    }
    settings.agent_platform_bindings_json = json.dumps(bindings)
    settings.agent_fde_opportunity_id = opportunity["id"]
    settings.agent_fde_opportunity_api_key = "SECRET_SELECTED_KEY"
    # Unselected adapter configuration is neither consulted nor modified.
    settings.agent_fde_competency_review_id = "UNSELECTED"
    settings.agent_fde_competency_review_api_key = "SECRET_UNSELECTED_KEY"
    target.write_text(
        "AGENT_PLATFORM_BINDINGS_JSON=" + shlex.quote(json.dumps(bindings)) + "\n"
        "AGENT_FDE_PILOT_PATH=" + shlex.quote(str(pilot_path)) + "\n"
    )
    return settings, target, pilot_path, directory, selected


@pytest.mark.parametrize("values", [[], ["chatbi"], ["unknown"], ["operating_report", "operating_report"]])
def test_capability_selection_rejects_empty_duplicate_unknown_and_chatbi(monkeypatch, values):
    module = load(monkeypatch)
    with pytest.raises(ValueError):
        module.selected_capabilities(values)


def test_selected_existing_bindings_preserve_every_other_metadata_and_rollout(monkeypatch, tmp_path):
    module = load(monkeypatch)
    settings, target, pilot_path, directory, selected = subset_fixture(module, tmp_path)
    originals = target.read_bytes(), pilot_path.read_bytes(), vars(settings).copy()
    plan = module.build_plan(settings, WORKSPACE, target=target, publications_dir=directory, capabilities=selected)
    assert plan == module.build_plan(
        settings, WORKSPACE, target=target, publications_dir=directory, capabilities=tuple(reversed(selected))
    )
    assert plan["capabilities"] == sorted(selected)
    assert set(plan["publications"]) == set(selected)
    assert plan["rollout_request"] is None
    assert plan["after"]["pilot"] == plan["before"]["pilot"]
    expected = json.loads(json.dumps(plan["before"]["bindings"]))
    for cap in selected:
        expected[WORKSPACE][cap]["expected_snapshot_id"] = plan["publications"][cap]["snapshot_id"]
    assert plan["after"]["bindings"] == expected
    assert "competency_review" not in expected[WORKSPACE]
    assert "opportunity_draft" not in plan["after"]["pilot"][WORKSPACE]["capabilities"]
    assert originals == (target.read_bytes(), pilot_path.read_bytes(), vars(settings))
    assert "SECRET" not in json.dumps(plan)
    presence = module.inspect(settings, selected)["credentials"]
    assert set(presence) == set(selected)
    assert presence["opportunity_draft"] == {"agent_id": settings.agent_fde_opportunity_id, "key_present": True}


@pytest.mark.parametrize(
    "problem",
    [
        "other_agent",
        "platform_predecessor",
        "unreviewed_snapshot",
        "missing_binding",
        "snapshot_pinned",
        "new_rollout",
        "adapter_alias_mismatch",
    ],
)
def test_generic_existing_binding_update_keeps_strict_cas_and_does_not_create(monkeypatch, tmp_path, problem):
    module = load(monkeypatch)
    settings, target, pilot_path, directory, selected = subset_fixture(module, tmp_path)
    bindings, pilot = json.loads(settings.agent_platform_bindings_json), json.loads(pilot_path.read_text())
    publications = module.publication_metadata(directory, selected)
    opportunity = bindings[WORKSPACE]["opportunity_draft"]
    request = None
    if problem == "other_agent":
        opportunity["agent_id"] = USER
    elif problem == "platform_predecessor":
        opportunity["expected_snapshot_id"] = publications["opportunity_draft"]["previous_snapshot_id"]
    elif problem == "unreviewed_snapshot":
        opportunity["expected_snapshot_id"] = USER
    elif problem == "missing_binding":
        bindings[WORKSPACE].pop("opportunity_draft")
    elif problem == "snapshot_pinned":
        opportunity["execution_mode"] = "snapshot_pinned"
    elif problem == "new_rollout":
        request = module.rollout_request("production", [], None)
    else:
        settings.agent_fde_opportunity_id = USER
        with pytest.raises(ValueError):
            module.build_plan(settings, WORKSPACE, target=target, publications_dir=directory, capabilities=selected)
        return
    with pytest.raises(ValueError):
        module.proposed_metadata(bindings, pilot, WORKSPACE, publications, request)


def test_existing_competency_uses_same_cas_without_touching_its_rollout(monkeypatch, tmp_path):
    module = load(monkeypatch)
    settings, _, pilot_path, request = fixture(module, tmp_path)
    publications = module.publication_metadata(module.PUBLICATIONS)
    bindings, pilot = module.proposed_metadata(
        json.loads(settings.agent_platform_bindings_json),
        json.loads(pilot_path.read_text()),
        WORKSPACE,
        publications,
        request,
    )
    competency = publications["competency_review"]
    competency["previous_binding_snapshot_id"] = competency["snapshot_id"]
    competency["snapshot_id"] = USER
    bindings[WORKSPACE]["competency_review"]["model"] = "preserve-existing-model"
    changed, preserved = module.proposed_metadata(bindings, pilot, WORKSPACE, {"competency_review": competency}, None)
    assert changed[WORKSPACE]["competency_review"] == {
        **bindings[WORKSPACE]["competency_review"],
        "expected_snapshot_id": USER,
    }
    assert changed[WORKSPACE]["operating_report"] == bindings[WORKSPACE]["operating_report"]
    assert preserved == pilot


@pytest.mark.parametrize("problem", [None, "worker_missing_key", "api_wrong_id"])
def test_subset_service_verification_requires_only_selected_execution_adapters(monkeypatch, tmp_path, problem):
    module = load(monkeypatch)
    worker, target, _, directory, selected = subset_fixture(module, tmp_path)
    plan = module.build_plan(worker, WORKSPACE, target=target, publications_dir=directory, capabilities=selected)
    api = SimpleNamespace(**vars(worker))
    for cap in selected:
        prefix = module.ADAPTER_ALIASES.get(cap, cap)
        setattr(api, "agent_fde_" + prefix + "_id", "")
        setattr(api, "agent_fde_" + prefix + "_api_key", "")
    if problem == "worker_missing_key":
        worker.agent_fde_opportunity_api_key = ""
    if problem == "api_wrong_id":
        api.agent_fde_opportunity_id = USER

    def reader(service):
        return module.inspect(api if service == module.SERVICES[0] else worker, selected)

    if problem:
        with pytest.raises(ValueError):
            module.verify_services(plan["before"]["runtime"], plan["publications"], reader=reader)
    else:
        observed = module.verify_services(plan["before"]["runtime"], plan["publications"], reader=reader)
        assert all(set(items) == set(selected) for items in observed.values())
        assert "SECRET" not in json.dumps(observed)


def test_subset_apply_rechecks_publications_and_preserves_pilot_bytes(monkeypatch, tmp_path):
    module = load(monkeypatch)
    settings, target, pilot_path, directory, selected = subset_fixture(module, tmp_path)
    plan = module.build_plan(settings, WORKSPACE, target=target, publications_dir=directory, capabilities=selected)
    original_pilot = pilot_path.read_bytes()
    calls = []

    def atomic(path, text, *_):
        calls.append(path)
        path.write_text(text)

    def reader(service):
        copied = SimpleNamespace(**vars(settings))
        values = dict(shlex.split(line)[0].split("=", 1) for line in target.read_text().splitlines())
        copied.agent_platform_bindings_json = values["AGENT_PLATFORM_BINDINGS_JSON"]
        return module.inspect(copied, selected)

    monkeypatch.setattr(module, "atomic_write", atomic)
    module.apply_plan(settings, plan, tmp_path / "evidence", target=target, publications_dir=directory, reader=reader)
    assert pilot_path not in calls
    assert pilot_path.read_bytes() == original_pilot
    assert json.loads((tmp_path / "evidence/result.json").read_text())["applied"] is True
    # A new publication invalidates the full reviewed plan, even if target bytes
    # are restored to the earlier binding state.
    target.write_text(
        "AGENT_PLATFORM_BINDINGS_JSON=" + shlex.quote(settings.agent_platform_bindings_json) + "\n"
        "AGENT_FDE_PILOT_PATH=" + shlex.quote(str(pilot_path)) + "\n"
    )
    publication = directory / "opportunity_draft/publication.json"
    metadata = json.loads(publication.read_text())
    metadata["snapshot_id"] = USER
    publication.write_text(json.dumps(metadata))
    with pytest.raises(ValueError):
        module.apply_plan(
            settings, plan, tmp_path / "rejected", target=target, publications_dir=directory, reader=reader
        )
    assert not (tmp_path / "rejected").exists()


def test_subset_cli_omits_rollout_and_passes_selection_to_both_probes(monkeypatch, tmp_path):
    module = load(monkeypatch)
    settings, target, _, directory, selected = subset_fixture(module, tmp_path)
    original_build = module.build_plan
    monkeypatch.setattr(
        module,
        "build_plan",
        lambda value, workspace, rollout, **kwargs: original_build(
            value, workspace, rollout, target=target, publications_dir=directory, **kwargs
        ),
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    output = tmp_path / "cli-plan.json"
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "prepare_agent_metadata.py",
            "plan",
            "--service",
            module.SERVICES[0],
            "--workspace",
            WORKSPACE,
            "--output",
            str(output),
            "--capability",
            selected[0],
            "--capability",
            selected[1],
        ],
    )
    module.main()
    document = json.loads(output.read_text())
    assert document["plan"]["capabilities"] == list(selected)
    assert document["plan"]["rollout_request"] is None
    assert set(document["adapter_status"]) == set(selected)
    invoked = []
    def probe(args, **kwargs):
        invoked.append(args)
        return SimpleNamespace(stdout="{}")
    monkeypatch.setattr(module.subprocess, "run", probe)
    module.service_snapshot(module.SERVICES[1], selected)
    assert invoked[0][-5:] == ["inspect", "--capability", selected[0], "--capability", selected[1]]


def test_quality_binding_addition_is_explicit_and_does_not_change_structure_rollout(monkeypatch, tmp_path):
    module = load(monkeypatch)
    settings, _, pilot_path, _ = fixture(module, tmp_path)
    bindings = json.loads(settings.agent_platform_bindings_json)
    pilot = json.loads(pilot_path.read_text())
    publications = {"visit_quality": {"id": USER, "snapshot_id": WORKSPACE}}
    with pytest.raises(ValueError):
        module.proposed_metadata(bindings, pilot, WORKSPACE, publications, None)
    rollout = module.rollout_request("production", [], None)
    after, after_pilot = module.proposed_metadata(bindings, pilot, WORKSPACE, publications, rollout)
    assert after[WORKSPACE]["visit_entry"] == bindings[WORKSPACE]["visit_entry"]
    assert after_pilot[WORKSPACE]["capabilities"]["visit_entry"] == pilot[WORKSPACE]["capabilities"]["visit_entry"]
    assert after_pilot[WORKSPACE]["capabilities"]["visit_quality"] == rollout


def test_service_probe_uses_inheritance_entrypoint_for_inline_environment(monkeypatch):
    module = load(monkeypatch)
    invocations = []

    def run(args, **kwargs):
        invocations.append(args)
        return SimpleNamespace(stdout='{"runtime":{},"credentials":{}}')

    monkeypatch.setattr(module.subprocess, "run", run)
    module.service_snapshot("sales-worker.service", ["visit_quality"])
    command = invocations[0]
    assert command[1].endswith("/inherit_service_environment.py")
    assert command[2:4] == ["--service", "sales-worker.service"]
    assert command[-3:] == ["inspect", "--capability", "visit_quality"]


def test_explicit_company_onboarding_selects_all_business_agents_only(monkeypatch, tmp_path):
    module = load(monkeypatch)
    assert len(module.SUPPORTED_CAPABILITIES) == 14
    published_templates(module, tmp_path)
    # Explicit onboarding uses reviewed publications; task import has its own contract below.
    caps = sorted(module.SUPPORTED_CAPABILITIES - {"task_import"})
    publications = module.publication_metadata(module.PUBLICATIONS, caps)
    old_bindings, old_pilot = {'other': {}}, {'other': {'enabled': False}}
    rollout = module.rollout_request('production', [], None)
    bindings, pilot = module.proposed_metadata(old_bindings, old_pilot, WORKSPACE,
        publications, rollout, allow_new_bindings=True)
    assert set(bindings[WORKSPACE]) == set(caps)
    assert set(pilot[WORKSPACE]['capabilities']) == set(caps)
    assert all(value == rollout for value in pilot[WORKSPACE]['capabilities'].values())
    assert bindings['other'] == old_bindings['other'] and pilot['other'] == old_pilot['other']
    assert WORKSPACE not in old_bindings and WORKSPACE not in old_pilot
    assert 'chatbi' not in bindings[WORKSPACE]
    assert 'task_import' not in bindings[WORKSPACE]
    assert module.proposed_metadata(bindings, pilot, WORKSPACE, publications, rollout,
        allow_new_bindings=True) == (bindings, pilot)
    with pytest.raises(ValueError, match='explicit rollout'):
        module.proposed_metadata({}, {}, WORKSPACE, publications, None, allow_new_bindings=True)
    pilot[WORKSPACE]['capabilities']['visit_entry'] = {'enabled': False}
    with pytest.raises(ValueError, match='must not be overwritten'):
        module.proposed_metadata(bindings, pilot, WORKSPACE, publications, rollout, allow_new_bindings=True)


def test_task_import_selection_still_requires_reviewed_published_metadata(monkeypatch, tmp_path):
    module = load(monkeypatch)
    assert module.selected_capabilities(["task_import"]) == ("task_import",)
    with pytest.raises(ValueError, match="regular metadata"):
        module.publication_metadata(tmp_path, ["task_import"])
    directory = tmp_path / "task_import"
    directory.mkdir()
    publication = {"published": True, "id": WORKSPACE, "snapshot_id": USER}
    (directory / "publication.json").write_text(json.dumps(publication))
    publications = module.publication_metadata(tmp_path, ["task_import"])
    rollout = module.rollout_request("production", [], None)
    bindings, pilot = module.proposed_metadata({}, {}, WORKSPACE, publications, rollout)
    assert bindings[WORKSPACE]["task_import"]["agent_id"] == WORKSPACE
    assert pilot[WORKSPACE]["capabilities"]["task_import"] == rollout


def test_company_onboarding_flag_is_bound_to_plan(monkeypatch, tmp_path):
    module = load(monkeypatch)
    settings, target, _, rollout = fixture(module, tmp_path)
    caps = ['competency_review']
    plan = module.build_plan(settings, WORKSPACE, rollout, target=target,
        capabilities=caps, allow_new_bindings=True)
    assert plan['allow_new_bindings'] is True
    ordinary = module.build_plan(settings, WORKSPACE, rollout, target=target, capabilities=caps)
    assert module.digest(plan) != module.digest(ordinary)
    with pytest.raises(ValueError, match='explicit capability'):
        module.build_plan(settings, WORKSPACE, rollout, target=target, allow_new_bindings=True)
    with pytest.raises(ValueError, match='boolean'):
        module.build_plan(settings, WORKSPACE, rollout, target=target, capabilities=caps, allow_new_bindings='true')


@pytest.mark.parametrize("cap", ["work_report", "competency_review", "finance_assistant"])
@pytest.mark.parametrize("problem", [None, "old_receipt", "missing_prompt_hash", "template_differs"])
def test_new_contract_requires_publication_for_exact_template(monkeypatch, tmp_path, cap, problem):
    module = load(monkeypatch)
    directory = published_templates(module, tmp_path)
    receipt = directory / cap / "publication.json"
    raw = json.loads(receipt.read_text())
    if problem == "old_receipt":
        raw["prompt_sha256"] = "0" * 64
    elif problem == "missing_prompt_hash":
        raw.pop("prompt_sha256")
    elif problem == "template_differs":
        template_path = directory / cap / "agent.json"
        template = json.loads(template_path.read_text())
        template["agent_soul"]["prompt"]["system_prompt"] += "unpublished change"
        template_path.write_text(json.dumps(template))
    receipt.write_text(json.dumps(raw))
    if problem:
        with pytest.raises(ValueError, match="reviewed prompt"):
            module.publication_metadata(directory, [cap])
    else:
        metadata = module.publication_metadata(directory, [cap])
        assert metadata[cap]["prompt_sha256"] == raw["prompt_sha256"]


@pytest.mark.parametrize("cap", ["work_report", "competency_review"])
def test_report_and_growth_cannot_run_without_worker_credentials(monkeypatch, cap):
    module = load(monkeypatch)
    runtime = {"synthetic": True}
    snapshots = {service: {"runtime": runtime, "credentials": {
        cap: {"agent_id": WORKSPACE, "key_present": service == "sales-api.service"}
    }} for service in module.SERVICES}
    with pytest.raises(ValueError, match="sales-worker.service execution adapter"):
        module.verify_services(runtime, {cap: {"id": WORKSPACE}}, reader=snapshots.__getitem__)


def test_cli_plan_accepts_explicit_external_publication_directory(monkeypatch, tmp_path):
    module = load(monkeypatch)
    settings, target, _, directory, selected = subset_fixture(module, tmp_path)
    original_build = module.build_plan
    observed = []

    def build(value, workspace, rollout, **kwargs):
        observed.append(kwargs["publications_dir"])
        return original_build(value, workspace, rollout, target=target, **kwargs)

    monkeypatch.setattr(module, "build_plan", build)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module.sys, "argv", ["prepare_agent_metadata.py", "plan",
        "--service", module.SERVICES[0], "--workspace", WORKSPACE,
        "--output", str(tmp_path / "external-plan.json"), "--publications-dir", str(directory),
        *[value for cap in selected for value in ("--capability", cap)]])
    module.main()
    assert observed == [directory]


def test_cli_apply_passes_external_publications_to_existing_guarded_apply(monkeypatch, tmp_path):
    from contextlib import nullcontext

    module = load(monkeypatch)
    settings, target, _, directory, selected = subset_fixture(module, tmp_path)
    plan = module.build_plan(settings, WORKSPACE, target=target, publications_dir=directory, capabilities=selected)
    sha = module.digest(plan)
    paths = [tmp_path / name for name in ("api.json", "worker.json")]
    for path, service in zip(paths, module.SERVICES, strict=True):
        path.write_text(json.dumps({"service": service, "plan_sha256": sha, "plan": plan}))
    called = []
    monkeypatch.setattr(module, "deployment_guard", nullcontext)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "apply_plan", lambda value, proposed, evidence, **kw:
                        called.append((value, proposed, kw)))
    monkeypatch.setattr(module.sys, "argv", ["prepare_agent_metadata.py", "apply",
        "--plan", str(paths[0]), "--peer-plan", str(paths[1]), "--approve-sha256", sha,
        "--evidence", str(tmp_path / "applied"), "--publications-dir", str(directory)])
    module.main()
    assert called == [(settings, plan, {"publications_dir": directory})]


def test_two_company_growth_predecessors_are_exact_per_company_not_a_shared_allowlist(monkeypatch, tmp_path):
    module = load(monkeypatch)
    directory = published_templates(module, tmp_path)
    path = directory / "competency_review" / "publication.json"
    raw = json.loads(path.read_text())
    raw["snapshot_id"] = USER  # Simulate the new shared publication, distinct from both old bindings.
    predecessors = {
        WORKSPACE: "01a0a583-b913-7e8c-9f0b-03fa4e97c527",
        "941c4874-a78d-44b5-8197-cf95acb33037": "01a0ae35-e1cd-7b66-ac45-cd63871ac389",
    }
    raw["previous_binding_snapshot_ids"] = predecessors
    path.write_text(json.dumps(raw))
    publications = module.publication_metadata(directory, ["competency_review"])
    published = publications["competency_review"]
    assert published["previous_binding_snapshot_ids"] == predecessors
    for workspace, predecessor in predecessors.items():
        item = {"enabled": True, "execution_mode": "filtered_facts", "agent_id": published["id"],
                "expected_snapshot_id": predecessor}
        bindings = {workspace: {"competency_review": item}}
        changed, _ = module.proposed_metadata(bindings, {}, workspace, publications, None)
        assert changed[workspace]["competency_review"]["expected_snapshot_id"] == published["snapshot_id"]
        item["expected_snapshot_id"] = next(value for key, value in predecessors.items() if key != workspace)
        with pytest.raises(ValueError, match="predecessor"):
            module.proposed_metadata(bindings, {}, workspace, publications, None)
    with pytest.raises(ValueError, match="outside"):
        module.proposed_metadata({}, {}, USER, publications,
                                 module.rollout_request("production", [], None), allow_new_bindings=True)
    for invalid in ({USER: USER}, {**predecessors, WORKSPACE: "not-a-uuid"}):
        raw["previous_binding_snapshot_ids"] = invalid
        path.write_text(json.dumps(raw))
        with pytest.raises(ValueError):
            module.publication_metadata(directory, ["competency_review"])
