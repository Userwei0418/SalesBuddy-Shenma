"""SalesBuddy 1.2.0 Agent hook, called inside the forward caller's lock.

The local operator keeps fdectl authorization on the local machine. This hook
waits at most ten minutes for its READY-linked, nonsecret publication receipts.
It never publishes remotely, reads credential files, or starts/stops services.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import prepare_agent_metadata as metadata

CAPABILITIES = ("work_report", "finance_assistant", "competency_review")
AGENT_IDS = {
    "work_report": "01a11ff5-f520-76c0-b1c6-8fd73f0c98d4",
    "finance_assistant": "01a11ff5-f67c-7bad-8e73-d1416b94f2cd",
    "competency_review": "01a09a6c-97c7-7631-b580-c3b2b14739ad",
}


class Refused(ValueError):
    """Fixed nonsecret diagnostics only."""


def require(condition, message):
    if not condition:
        raise Refused(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(metadata.read_regular(path))


def read_journal(path):
    # The forward journal embeds complete ledger/ACL receipts, unlike compact
    # Agent metadata. Keep its separate bound without widening metadata reads.
    require(path.is_absolute() and not path.is_symlink() and path.is_file(),
            "Regular absolute caller journal required")
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    require(len(raw) <= 1024 * 1024, "Caller journal exceeds 1 MiB")
    value = json.loads(raw)
    require(isinstance(value, dict), "Caller journal must be an object")
    return value


def write(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def stopped(run):
    for service in metadata.SERVICES:
        require(run(["/usr/bin/systemctl", "show", service, "-p", "ActiveState", "--value"]).strip()
                == "inactive", "Both execution services must remain inactive")


def held_window(ready, run):
    require(os.geteuid() == 0, "Root deployment operator required")
    journal = read_journal(Path(ready["journal"]))
    require(journal.get("id") == ready["bundle_id"] and journal.get("phase") == "agent_configuration"
            and journal.get("migration_attempted") is True, "Caller is outside the Agent maintenance phase")
    require(journal.get("database_phase", {}).get("result", {}).get("success") is True,
            "Database phase must already have succeeded")
    os.kill(ready["caller_pid"], 0)
    lock = Path(ready["lock"])
    require(lock == metadata.LOCK and not lock.is_symlink(), "Canonical caller lock required")
    with lock.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(stream, fcntl.LOCK_UN)
            raise Refused("Forward caller must retain its deployment lock")
    stopped(run)


def checked_publications(ready, ready_path):
    directory = ready_path.parent / "publications"
    receipt = read(directory / "RECEIPT.json")
    require(set(receipt) == {"bundle_id", "ready_sha256", "publications_sha256"}
            and receipt["bundle_id"] == ready["bundle_id"]
            and receipt["ready_sha256"] == sha(ready_path), "Publication receipts are not linked to this READY")
    require(set(receipt["publications_sha256"]) == set(CAPABILITIES), "All three exact publications required")
    for cap in CAPABILITIES:
        folder = directory / cap
        require(not folder.is_symlink() and folder.is_dir(), "Regular publication directory required")
        require(sha(folder / "publication.json") == receipt["publications_sha256"][cap],
                "Transferred publication changed")
        raw = read(folder / "publication.json")
        require(raw.get("id") == AGENT_IDS[cap] and raw.get("workspace_id") ==
                "a06dcd4a-af4b-4a91-85e6-109e6e7e5720", "Unexpected platform Agent identity")
        require(raw.get("record_type") == "platform_publication_and_get_verification"
                and raw.get("prompt_sha256") == ready["prompt_sha256"][cap], "Actual reviewed get receipt required")
        observed = datetime.fromisoformat(raw["observed_at"])
        require(observed.tzinfo is not None
                and datetime.fromisoformat(ready["created_at"]) <= observed <= datetime.now(UTC),
                "A fresh platform get after READY is required")
        require(sha(folder / "system_prompt.txt") == ready["prompt_sha256"][cap], "Reviewed prompt changed")
    return directory, metadata.publication_metadata(directory, CAPABILITIES)


def service_command(release, service, script, arguments):
    python = release / ".venv/bin/python"
    return [python, release / "backend/deploy/inherit_service_environment.py", "--service", service,
            "--unit", "sales-agent120-" + uuid4().hex[:16], "--", python,
            release / "backend/deploy" / script, *arguments]


def snapshots(run, release):
    arguments = [value for cap in CAPABILITIES for value in ("--capability", cap)]
    values = {service: json.loads(run(service_command(release, service, "prepare_agent_metadata.py",
                                                    ["inspect", *arguments]))) for service in metadata.SERVICES}
    first = values[metadata.SERVICES[0]]["runtime"]
    metadata.verify_services(first, {cap: {"id": AGENT_IDS[cap]} for cap in CAPABILITIES}, reader=values.__getitem__)
    return values


def service_metadata(host):
    properties = ("User", "Group", "WorkingDirectory", "EnvironmentFiles", "FragmentPath", "DropInPaths")
    result = {}
    for service in (*metadata.SERVICES, "sales-feishu-worker.service"):
        values = dict(line.split("=", 1) for line in host.run(
            ["systemctl", "show", service, *["-p" + name for name in properties]]).splitlines() if "=" in line)
        paths = set(re.findall(r"/[^\s;()]+", " ".join(values.get(name, "") for name in
                    ("EnvironmentFiles", "FragmentPath", "DropInPaths"))))
        stamps = {}
        for name in sorted(paths):
            info = Path(name).stat()
            stamps[name] = [info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode]
        result[service] = {"properties": values, "file_metadata": stamps}
    return result


def allowed_configuration_delta(before, after, result, service_before, service_after):
    require(set(before) == set(after) == {"service_metadata_sha256", "agent_metadata"},
            "Unexpected Host configuration shape")
    require(metadata.digest(service_before) == before["service_metadata_sha256"]
            and metadata.digest(service_after) == after["service_metadata_sha256"],
            "Service metadata observation drift")
    for service in service_before:
        old, new = service_before[service], service_after[service]
        require(old["properties"] == new["properties"]
                and set(old["file_metadata"]) == set(new["file_metadata"]), "Service configuration paths changed")
        for path, stamp in old["file_metadata"].items():
            changed = new["file_metadata"][path]
            require(changed == stamp or (path == str(metadata.TARGET) and changed[3] == stamp[3]),
                    "Unrelated service configuration file changed")
    require(before["agent_metadata"]["rollout_source_sha256"] == after["agent_metadata"]["rollout_source_sha256"],
            "Rollout source must remain unchanged")
    require(after["agent_metadata"] == result["verified"], "Host and applied Agent metadata differ")


def hook(host, stage_plan, evidence_dir):
    """Caller sets phase=agent_configuration and holds lock across this call.

    stage_plan: bundle_id, backend, workspace_ids, new_capability_rollout.
    Caller must use candidate metadata probes in Host.configuration after entry.
    """
    require(set(stage_plan) == {"bundle_id", "backend", "workspace_ids", "new_capability_rollout"},
            "Exact Agent stage plan required")
    release = host.root / "releases" / stage_plan["backend"]
    require(release == Path(__file__).resolve().parents[2], "Hook must run from candidate release")
    workspace_ids = stage_plan["workspace_ids"]
    require(isinstance(workspace_ids, list) and len(workspace_ids) == 2 and set(workspace_ids) == {
        "00000000-0000-0000-0000-000000000001", "941c4874-a78d-44b5-8197-cf95acb33037"},
        "Exact two approved business companies required")
    rollout = stage_plan["new_capability_rollout"]
    require(metadata.rollout_request(rollout["rollout"], rollout.get("user_ids", []), rollout.get("expires_at"))
            == rollout, "Explicit canonical new-capability rollout required")
    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    prompt_sha = {cap: sha(release / "backend/agent_platform" / cap / "system_prompt.txt") for cap in CAPABILITIES}
    ready = {"bundle_id": stage_plan["bundle_id"], "nonce": uuid4().hex, "release": str(release),
             "workspace_ids": workspace_ids, "prompt_sha256": prompt_sha,
             "caller_pid": os.getpid(), "journal": str(host.state / "TRANSACTION.json"),
             "lock": str(metadata.LOCK), "created_at": datetime.now(UTC).isoformat()}
    held_window(ready, host.run)
    before = host.configuration()
    service_before = service_metadata(host)
    initial = snapshots(host.run, release)  # missing credentials refuse before READY/publication
    require(initial[metadata.SERVICES[0]]["runtime"] == before["agent_metadata"],
            "Candidate probe differs before stage")
    publications_dir = evidence_dir / "publications"
    for cap in CAPABILITIES:
        destination = publications_dir / cap
        destination.mkdir(mode=0o700, parents=True)
        for name in ("agent.json", "system_prompt.txt"):
            shutil.copyfile(release / "backend/agent_platform" / cap / name, destination / name)
            (destination / name).chmod(0o600)
    ready_path = evidence_dir / "READY.json"
    write(ready_path, ready)
    deadline = time.monotonic() + 600
    while not (publications_dir / "RECEIPT.json").exists():
        held_window(ready, host.run)
        require(time.monotonic() < deadline, "Platform receipt window expired; leave services stopped")
        time.sleep(3)
    directory, _ = checked_publications(ready, ready_path)
    plans = []
    latest = None
    for index, (workspace, cap) in enumerate((workspace, cap) for workspace in workspace_ids for cap in CAPABILITIES):
        held_window(ready, host.run)
        # Existing rollout is preserved. Only a missing binding receives the
        # plan's explicit new-capability rollout; service keys never enter here.
        paths = []
        for service in metadata.SERVICES:
            path = evidence_dir / f"{index}-{service}.json"
            arguments = ["plan", "--service", service, "--workspace", workspace,
                         "--capability", cap, "--publications-dir", directory, "--output", path]
            # A read-only ordinary plan determines whether a binding is new.
            # The managed binding file contains only two nonsecret assignments.
            bindings = read_bindings()
            if cap not in bindings.get(workspace, {}):
                arguments += ["--allow-new-bindings", "--rollout", rollout["rollout"]]
                if rollout["rollout"] == "pilot":
                    arguments += ["--expires-at", rollout["expires_at"]]
                    arguments += [value for uid in rollout["user_ids"] for value in ("--user-id", uid)]
            host.run(service_command(release, service, "prepare_agent_metadata.py", arguments))
            paths.append(path)
        api, worker = (read(path) for path in paths)
        plan = metadata.checked_plan(api, worker, api["plan_sha256"])
        require(plan["paths"]["bindings"] == str(metadata.TARGET), "Unexpected binding target")
        # apply-held checks caller/READY/receipt again under API inherited env.
        applied = evidence_dir / f"{index}-applied"
        host.run(service_command(release, metadata.SERVICES[0], "release120_agent_stage.py", [
            "--apply-held", "--ready", ready_path, "--ready-sha", sha(ready_path),
            "--api-plan", paths[0], "--worker-plan", paths[1], "--plan-sha", api["plan_sha256"],
            "--evidence", applied]))
        latest = read(applied / "result.json")
        require(latest.get("applied") is True, "Agent metadata apply did not complete")
        plans.append({"workspace_id": workspace, "capability": cap,
                      "plan_sha256": api["plan_sha256"], "receipt": str(applied / "result.json")})
    held_window(ready, host.run)
    final = snapshots(host.run, release)
    require(final[metadata.SERVICES[0]]["runtime"] == latest["verified"],
            "Final full capability observation differs")
    after = host.configuration_for(stage_plan["backend"])
    allowed_configuration_delta(before, after, latest, service_before, service_metadata(host))
    result = {"configuration_before": before, "configuration_after": after, "applied": plans,
              "ready_sha256": sha(ready_path), "services_remain_stopped": True, "credential_values_returned": False}
    write(evidence_dir / "RESULT.json", result)
    return result


def read_bindings():
    import shlex

    values = {}
    for line in metadata.read_regular(metadata.TARGET).splitlines():
        if line.strip():
            tokens = shlex.split(line)
            require(len(tokens) == 1 and "=" in tokens[0], "Unexpected nonsecret metadata format")
            key, value = tokens[0].split("=", 1)
            require(key in {"AGENT_PLATFORM_BINDINGS_JSON", "AGENT_FDE_PILOT_PATH"} and key not in values,
                    "Unexpected nonsecret metadata field")
            values[key] = value
    return metadata.metadata_object(values["AGENT_PLATFORM_BINDINGS_JSON"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-held", action="store_true", required=True)
    for name in ("ready", "api-plan", "worker-plan", "evidence"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--ready-sha", required=True)
    parser.add_argument("--plan-sha", required=True)
    args = parser.parse_args()
    try:
        require(sha(args.ready) == args.ready_sha, "READY changed")
        ready = read(args.ready)
        require(Path(ready["release"]) == Path(__file__).resolve().parents[2], "Wrong candidate stage entrypoint")
        def run(argv):
            return subprocess.run(  # noqa: S603 - held_window uses only fixed systemctl arguments
                [str(value) for value in argv], check=True, capture_output=True, text=True
            ).stdout
        held_window(ready, run)
        directory, _ = checked_publications(ready, args.ready)
        plan = metadata.checked_plan(read(args.api_plan), read(args.worker_plan), args.plan_sha)
        require(len(plan["capabilities"]) == 1 and set(plan["capabilities"]) <= set(CAPABILITIES)
                and plan["workspace_id"] in ready["workspace_ids"], "Unexpected maintenance plan scope")
        metadata.apply_plan(metadata.get_settings(), plan, args.evidence, publications_dir=directory)
        print(json.dumps({"applied": True, "credential_values_returned": False}))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        raise SystemExit("Agent stage refused; preserve evidence and keep services stopped") from None


if __name__ == "__main__":
    main()
