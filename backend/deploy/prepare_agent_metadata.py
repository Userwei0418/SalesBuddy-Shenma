"""Plan and apply reviewed metadata for an explicit subset of integrated Agents.

Use inherit_service_environment.py; never read credential EnvironmentFiles.
Apply owns the deployment lock, requires stopped services, compares fresh API /
Worker metadata and retains nonsecret recovery evidence before atomic writes.
"""

import argparse
import fcntl
import hashlib
import json
import os
import shlex
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from prepare_advice_runtime import atomic_write, validate_rollout_bindings
from verify_agent_runtime import digest, metadata_object, verify

from sales_backend.config import get_settings
from sales_backend.services.agent_platform.inference import binding_for

CAPABILITIES = ("competency_review", "operating_report")
ADDITIONS = {"competency_review", "visit_quality", "task_import"}
# This reviewed publication catalog is intentionally independent of runtime
# model capabilities. A new direct-model feature must not silently become a
# deployed platform Agent or require a nonexistent publication during onboarding.
SUPPORTED_CAPABILITIES = frozenset({
    "battle_map_review", "opportunity_draft", "personal_risks", "visit_entry",
    "visit_quality", "today_tasks", "operating_report", "customer_advice",
    "opportunity_advice", "visit_advice", "competency_review",
    "task_import", "work_report", "finance_assistant",
})
ADAPTER_ALIASES = {"battle_map_review": "battle_map", "opportunity_draft": "opportunity"}
SERVICES = ("sales-api.service", "sales-worker.service")
API_EXECUTED_CAPABILITIES = frozenset({"task_import", "finance_assistant"})
TEMPLATE_VERIFIED_CAPABILITIES = frozenset({"work_report", "competency_review", "finance_assistant"})
RELEASE120_WORKSPACES = frozenset({
    "00000000-0000-0000-0000-000000000001", "941c4874-a78d-44b5-8197-cf95acb33037",
})
TARGET = Path("/etc/sales-fde-advice-bindings.env")
LOCK = Path("/www/wwwroot/salegent/.deploy.lock")
PUBLICATIONS = Path(__file__).resolve().parents[1] / "agent_platform"
BINDING_KEYS = {
    "enabled",
    "execution_mode",
    "agent_id",
    "expected_snapshot_id",
    "snapshot_id",
    "approved_snapshot_id",
    "model",
}


def nonsecret_metadata(bindings, pilot):
    """Reject unrecognized fields before putting inherited metadata in evidence."""
    for workspace in bindings.values():
        if not isinstance(workspace, dict):
            raise ValueError("Invalid workspace bindings")
        for item in workspace.values():
            if not isinstance(item, dict) or not set(item) <= BINDING_KEYS:
                raise ValueError("Binding metadata has unsupported fields")
    rollout_keys = {"enabled", "rollout", "user_ids", "expires_at", "block_platform_requests"}
    for workspace in pilot.values():
        if not isinstance(workspace, dict) or not set(workspace) <= rollout_keys | {"capabilities"}:
            raise ValueError("Rollout metadata has unsupported fields")
        caps = workspace.get("capabilities", {})
        if not isinstance(caps, dict) or any(
            not isinstance(x, dict) or not set(x) <= rollout_keys for x in caps.values()
        ):
            raise ValueError("Capability rollout metadata has unsupported fields")
    validate_rollout_bindings(bindings, pilot)


def selected_capabilities(capabilities=None):
    values = CAPABILITIES if capabilities is None else tuple(capabilities)
    if not values or len(values) != len(set(values)) or not set(values) <= SUPPORTED_CAPABILITIES:
        raise ValueError(
            "Select a nonempty unique subset of supported platform Agent capabilities"
        )
    return tuple(sorted(values))


def inspect(settings, capabilities=None):
    return {
        "runtime": verify(settings),
        "credentials": {
            cap: {
                "agent_id": getattr(settings, "agent_fde_" + ADAPTER_ALIASES.get(cap, cap) + "_id"),
                "key_present": bool(getattr(settings, "agent_fde_" + ADAPTER_ALIASES.get(cap, cap) + "_api_key")),
            }
            for cap in selected_capabilities(capabilities)
        },
    }


def read_regular(path):
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("An existing regular metadata file is required")
    if path.stat().st_size > 64000:
        raise ValueError("Metadata exceeds runtime limit")
    return path.read_text(encoding="utf-8")


def read_target(path, bindings, pilot_path):
    raw = read_regular(path)
    # This generated file contains only these two nonsecret assignments. Never
    # accept or copy arbitrary shell/environment content into recovery evidence.
    values = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        tokens = shlex.split(line, comments=False)
        if len(tokens) != 1 or "=" not in tokens[0]:
            raise ValueError("Unsupported generated metadata format")
        name, value = tokens[0].split("=", 1)
        if name in values or name not in {"AGENT_PLATFORM_BINDINGS_JSON", "AGENT_FDE_PILOT_PATH"}:
            raise ValueError("Generated metadata has unexpected assignments")
        values[name] = value
    if metadata_object(values.get("AGENT_PLATFORM_BINDINGS_JSON")) != bindings or values.get(
        "AGENT_FDE_PILOT_PATH"
    ) != str(pilot_path):
        raise ValueError("Effective bindings differ from the managed metadata file")
    return raw


def publication_metadata(directory, capabilities=None):
    result = {}
    for cap in selected_capabilities(capabilities):
        raw = metadata_object(read_regular(directory / cap / "publication.json"))
        if raw.get("published") is not True:
            raise ValueError("Publication must already be published")
        if cap in TEMPLATE_VERIFIED_CAPABILITIES:
            prompt = read_regular(directory / cap / "system_prompt.txt")
            template = metadata_object(read_regular(directory / cap / "agent.json"))
            if template["agent_soul"]["prompt"]["system_prompt"] != prompt:
                raise ValueError(f"{cap} Agent template differs from its reviewed prompt")
            if raw.get("prompt_sha256") != hashlib.sha256(prompt.encode()).hexdigest():
                raise ValueError(f"{cap} publication does not match the reviewed prompt; publish and read back first")
        for key in ("id", "snapshot_id", "previous_snapshot_id", "previous_binding_snapshot_id"):
            if key not in raw and key not in {"id", "snapshot_id"}:
                continue
            if str(UUID(raw[key])) != raw[key]:
                raise ValueError("Publication must use canonical runtime IDs")
        result[cap] = {
            key: raw[key]
            for key in ("id", "snapshot_id", "previous_snapshot_id", "previous_binding_snapshot_id", "model")
            if key in raw
        }
        if cap in TEMPLATE_VERIFIED_CAPABILITIES:
            result[cap]["prompt_sha256"] = raw["prompt_sha256"]
        if "previous_binding_snapshot_ids" in raw:
            predecessors = raw["previous_binding_snapshot_ids"]
            if (cap != "competency_review" or not isinstance(predecessors, dict)
                    or set(predecessors) != RELEASE120_WORKSPACES):
                raise ValueError("Exact approved company predecessor map required")
            for workspace, snapshot in predecessors.items():
                if str(UUID(workspace)) != workspace or str(UUID(snapshot)) != snapshot:
                    raise ValueError("Company predecessors must use canonical UUIDs")
            result[cap]["previous_binding_snapshot_ids"] = predecessors
    return result


def rollout_request(mode, user_ids, expires_at, *, now=None):
    if mode == "production":
        if user_ids or expires_at:
            raise ValueError("Production cannot carry a pilot allowlist or expiry")
        return {"enabled": True, "rollout": "production"}
    if mode != "pilot" or not user_ids or len(user_ids) > 20 or len(set(user_ids)) != len(user_ids):
        raise ValueError("An explicit nonempty pilot allowlist is required")
    for uid in user_ids:
        if str(UUID(uid)) != uid:
            raise ValueError("Pilot users must be canonical UUIDs")
    expiry = datetime.fromisoformat(expires_at or "")
    if expiry.tzinfo is None or expiry <= (now or datetime.now(UTC)):
        raise ValueError("Pilot expiry must be a future timezone-aware date")
    return {"enabled": True, "rollout": "pilot", "user_ids": sorted(user_ids), "expires_at": expiry.isoformat()}


def proposed_metadata(bindings, pilot, workspace, publications, rollout, *, allow_new_bindings=False):
    result, rollout_result = json.loads(json.dumps(bindings)), json.loads(json.dumps(pilot))
    if str(UUID(workspace)) != workspace:
        raise ValueError("Workspace must be a canonical UUID")
    config = rollout_result.get(workspace, {})
    if config.get("enabled") is True or config.get("capabilities", {}).get("chatbi", {}).get("enabled") is True:
        raise ValueError("ChatBI must remain disabled for this delivery")
    additions = set(publications) if allow_new_bindings else set(publications) & ADDITIONS
    if allow_new_bindings and rollout is None:
        raise ValueError("Company onboarding requires an explicit rollout request")
    if not allow_new_bindings and rollout is not None and len(additions) != 1:
        raise ValueError("Rollout arguments require exactly one explicit capability addition")
    for cap in selected_capabilities(publications):
        published = publications[cap]
        predecessors = published.get("previous_binding_snapshot_ids")
        if predecessors is not None and workspace not in predecessors:
            raise ValueError("Company is outside the reviewed predecessor map")
        before = result.get(workspace, {}).get(cap)
        if cap in additions and before is None:
            if rollout is None:
                raise ValueError("Adding a capability requires an explicit rollout request")
            existing_rollout = config.get("capabilities", {}).get(cap)
            if existing_rollout is not None and existing_rollout != rollout:
                raise ValueError("Existing capability rollout must not be overwritten")
            result.setdefault(workspace, {})[cap] = {
                "enabled": True,
                "execution_mode": "filtered_facts",
                "agent_id": published["id"],
                "expected_snapshot_id": published["snapshot_id"],
            }
            rollout_result.setdefault(workspace, {}).setdefault("capabilities", {})[cap] = rollout
            continue
        if binding_for(json.dumps(result), workspace, cap) is None or before.get("execution_mode") != "filtered_facts":
            raise ValueError("An existing valid filtered-facts binding is required")
        # Only an explicit measured binding predecessor (or its platform
        # predecessor when absent) and the target are accepted; never any old ID.
        predecessor = (predecessors[workspace] if predecessors is not None else
                       published.get("previous_binding_snapshot_id", published.get("previous_snapshot_id")))
        if before["agent_id"] != published["id"] or before["expected_snapshot_id"] not in {
            predecessor,
            published["snapshot_id"],
        }:
            raise ValueError("Binding is not the reviewed publication predecessor")
        if cap in additions and rollout is not None and config.get("capabilities", {}).get(cap) != rollout:
            raise ValueError("Existing capability rollout must not be overwritten")
        # Existing capabilities never gain a rollout or a changed Agent/model.
        before["expected_snapshot_id"] = published["snapshot_id"]
    nonsecret_metadata(result, rollout_result)
    return result, rollout_result


def build_plan(settings, workspace, rollout=None, *, target=TARGET, publications_dir=None,
               capabilities=None, allow_new_bindings=False):
    if type(allow_new_bindings) is not bool:
        raise ValueError("Company onboarding must be an explicit boolean")
    if allow_new_bindings and capabilities is None:
        raise ValueError("Company onboarding requires an explicit capability selection")
    capabilities = selected_capabilities(capabilities)
    if (
        rollout is not None
        and rollout_request(rollout["rollout"], rollout.get("user_ids", []), rollout.get("expires_at")) != rollout
    ):
        raise ValueError("Rollout request is not canonical")
    current = inspect(settings, capabilities)
    pilot_path = Path(settings.agent_fde_pilot_path)
    bindings = metadata_object(settings.agent_platform_bindings_json)
    pilot_raw = read_regular(pilot_path)
    pilot = metadata_object(pilot_raw)
    nonsecret_metadata(bindings, pilot)
    target_raw = read_target(target, bindings, pilot_path)
    publications = publication_metadata(PUBLICATIONS if publications_dir is None else publications_dir, capabilities)
    for cap in capabilities:
        adapter = current["credentials"][cap]
        if adapter["agent_id"] not in {"", publications[cap]["id"]} or (
            adapter["key_present"] and not adapter["agent_id"]
        ):
            raise ValueError("Typed Agent ID does not match the published metadata")
    after_bindings, after_pilot = proposed_metadata(
        bindings, pilot, workspace, publications, rollout, allow_new_bindings=allow_new_bindings
    )
    return {
        "schema_version": 1,
        "workspace_id": workspace,
        "capabilities": list(capabilities),
        "allow_new_bindings": allow_new_bindings,
        "publications": publications,
        "rollout_request": rollout,
        "paths": {"bindings": str(target), "pilot": str(pilot_path)},
        "before": {
            "runtime": current["runtime"],
            "bindings": bindings,
            "pilot": pilot,
            "target_sha256": hashlib.sha256(target_raw.encode()).hexdigest(),
            "pilot_file_sha256": hashlib.sha256(pilot_raw.encode()).hexdigest(),
        },
        "after": {"bindings": after_bindings, "pilot": after_pilot},
    }


def checked_plan(document, peer, approved_sha):
    if (
        document.get("service") not in SERVICES
        or peer.get("service") not in SERVICES
        or document["service"] == peer["service"]
    ):
        raise ValueError("Distinct API and Worker plans are required")
    plan = document["plan"]
    if (
        digest(plan) != approved_sha
        or document.get("plan_sha256") != approved_sha
        or peer.get("plan_sha256") != approved_sha
        or peer.get("plan") != plan
    ):
        raise ValueError("The reviewed plans do not match")
    return plan


def service_snapshot(service, capabilities=None):
    arguments = [value for cap in selected_capabilities(capabilities) for value in ("--capability", cap)]
    # The entrypoint owns the private temporary file needed for systemd's
    # inline Environment values, as well as their cleanup after the probe.
    invocation = [
        sys.executable,
        str(Path(__file__).with_name("inherit_service_environment.py")),
        "--service",
        service,
        "--unit",
        "agent-metadata-probe-" + uuid4().hex,
        "--",
        sys.executable,
        str(Path(__file__).resolve()),
        "inspect",
        *arguments,
    ]
    response = subprocess.run(invocation, check=True, capture_output=True, text=True)  # noqa: S603 - validated service and script
    return json.loads(response.stdout)


def verify_services(expected_runtime, publications, *, expected_credentials=None, reader=None):
    observations = {}
    capabilities = selected_capabilities(publications)
    for service in SERVICES:
        snapshot = reader(service) if reader else service_snapshot(service, capabilities)
        if snapshot.get("runtime") != expected_runtime:
            raise ValueError("Fresh API/Worker effective metadata differ from the plan")
        credentials = snapshot.get("credentials", {})
        if set(credentials) != set(capabilities):
            raise ValueError("Incomplete adapter presence report")
        for cap in capabilities:
            adapter = credentials[cap]
            if set(adapter) != {"agent_id", "key_present"} or type(adapter["key_present"]) is not bool:
                raise ValueError("Invalid adapter presence report")
            identifier, present = adapter["agent_id"], adapter["key_present"]
            if identifier not in {"", publications[cap]["id"]} or (present and not identifier):
                raise ValueError("Service adapter ID does not match its binding")
            # Task import and finance explanation run synchronously in API; the other integrated
            # capabilities run as Worker jobs. The non-executing service may
            # omit credentials while still sharing the same binding metadata.
            executor = "sales-api.service" if cap in API_EXECUTED_CAPABILITIES else "sales-worker.service"
            if service == executor and (not identifier or not present):
                raise ValueError(f"{executor} execution adapter for {cap} must have its ID and key")
        observations[service] = credentials
    if expected_credentials is not None and observations != expected_credentials:
        raise ValueError("Service adapter ID or key presence changed during apply")
    return observations


@contextmanager
def deployment_guard():
    if os.geteuid() != 0 or LOCK.is_symlink():
        raise ValueError("Apply requires the deployment operator and canonical lock")
    with LOCK.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for service in SERVICES:
            state = subprocess.run(  # noqa: S603 - fixed systemctl arguments
                ["/usr/bin/systemctl", "show", service, "-p", "ActiveState", "--value"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if state != "inactive":
                raise ValueError("API and Worker must both be stopped")
        yield


def apply_plan(settings, plan, evidence, *, target=TARGET, publications_dir=None, reader=None):
    # Rebuild from effective typed Settings and disk, then compare every reviewed
    # field. A concurrent pilot/binding/publication change fails before any write.
    request = plan["rollout_request"]
    if request is not None:
        rollout_request(request["rollout"], request.get("user_ids", []), request.get("expires_at"))
    if (
        build_plan(
            settings,
            plan["workspace_id"],
            request,
            target=target,
            publications_dir=publications_dir,
            capabilities=plan["capabilities"],
            allow_new_bindings=plan["allow_new_bindings"],
        )
        != plan
    ):
        raise ValueError("Metadata changed after review; generate a new plan")
    before_credentials = verify_services(plan["before"]["runtime"], plan["publications"], reader=reader)
    pilot_path = Path(plan["paths"]["pilot"])
    target_raw = read_target(target, plan["before"]["bindings"], pilot_path)
    pilot_raw = read_regular(pilot_path)
    evidence.mkdir(mode=0o700, parents=False, exist_ok=False)
    atomic_write(evidence / "reviewed-plan.json", json.dumps(plan, indent=2) + "\n", 0o600, 0, 0)
    atomic_write(
        evidence / "service-configuration-before.json", json.dumps(before_credentials, indent=2) + "\n", 0o600, 0, 0
    )
    target_stat, pilot_stat = target.stat(), pilot_path.stat()

    def write(path, text, stat):
        atomic_write(path, text, stat.st_mode & 0o777, stat.st_uid, stat.st_gid)

    value = (
        "AGENT_PLATFORM_BINDINGS_JSON="
        + shlex.quote(json.dumps(plan["after"]["bindings"], separators=(",", ":")))
        + "\n"
    )
    value += "AGENT_FDE_PILOT_PATH=" + shlex.quote(str(pilot_path)) + "\n"
    # A stopped pair cannot observe a half-applied pair. If either write or the
    # effective service verification fails, restore both original metadata files.
    try:
        write(target, value, target_stat)
        if plan["after"]["pilot"] != plan["before"]["pilot"]:
            write(pilot_path, json.dumps(plan["after"]["pilot"], indent=2) + "\n", pilot_stat)
        after = {
            **plan["before"]["runtime"],
            "bindings_sha256": digest(plan["after"]["bindings"]),
            "rollout_sha256": digest(plan["after"]["pilot"]),
        }
        after_credentials = verify_services(
            after, plan["publications"], expected_credentials=before_credentials, reader=reader
        )
        atomic_write(
            evidence / "result.json",
            json.dumps(
                {"applied": True, "plan_sha256": digest(plan), "verified": after, "services": after_credentials},
                indent=2,
            )
            + "\n",
            0o600,
            0,
            0,
        )
    except BaseException:
        write(target, target_raw, target_stat)
        write(pilot_path, pilot_raw, pilot_stat)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspection = sub.add_parser("inspect")
    inspection.add_argument("--capability", action="append", choices=sorted(SUPPORTED_CAPABILITIES))
    plan = sub.add_parser("plan", help="Read-only dry run; no service metadata is written")
    plan.add_argument("--service", choices=SERVICES, required=True)
    plan.add_argument("--workspace", required=True)
    plan.add_argument(
        "--capability",
        action="append",
        choices=sorted(SUPPORTED_CAPABILITIES),
        help="Repeat to select existing bindings; defaults to competency_review and operating_report",
    )
    plan.add_argument("--rollout", choices=("pilot", "production"))
    plan.add_argument("--allow-new-bindings", action="store_true",
                      help="Explicit company onboarding; requires selected capabilities and rollout")
    plan.add_argument("--user-id", action="append", default=[])
    plan.add_argument("--expires-at")
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--publications-dir", type=Path,
                      help="Reviewed external publication receipts with matching templates")
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--peer-plan", type=Path, required=True)
    apply.add_argument("--approve-sha256", required=True)
    apply.add_argument("--evidence", type=Path, required=True)
    apply.add_argument("--publications-dir", type=Path,
                       help="Same reviewed publication directory used to generate both plans")
    args = parser.parse_args()
    try:
        settings = get_settings()
        if args.command == "inspect":
            print(json.dumps(inspect(settings, args.capability), sort_keys=True))
        elif args.command == "plan":
            rollout = (
                rollout_request(args.rollout or "pilot", args.user_id, args.expires_at)
                if (args.rollout or args.user_id or args.expires_at)
                else None
            )
            publication_options = {"publications_dir": args.publications_dir} if args.publications_dir else {}
            proposal = build_plan(settings, args.workspace, rollout, capabilities=args.capability,
                                  allow_new_bindings=args.allow_new_bindings, **publication_options)
            # Credential presence is an observation, not shared metadata. Apply
            # checks credentials on the actual execution service afresh.
            observed = inspect(settings, args.capability)["credentials"]
            document = {
                "service": args.service,
                "plan_sha256": digest(proposal),
                "plan": proposal,
                "adapter_status": observed,
            }
            with args.output.open("x", encoding="utf-8") as stream:
                os.chmod(args.output, 0o600)
                json.dump(document, stream, indent=2)
                stream.write("\n")
            print(
                json.dumps(
                    {
                        "plan_sha256": document["plan_sha256"],
                        "publications": proposal["publications"],
                        "adapter_status": observed,
                        "applied": False,
                    }
                )
            )
        else:
            proposal = checked_plan(
                json.loads(read_regular(args.plan)), json.loads(read_regular(args.peer_plan)), args.approve_sha256
            )
            with deployment_guard():
                publication_options = {"publications_dir": args.publications_dir} if args.publications_dir else {}
                apply_plan(settings, proposal, args.evidence, **publication_options)
            print(json.dumps({"applied": True, "plan_sha256": args.approve_sha256, "credentials_printed": False}))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        raise SystemExit("Agent metadata operation refused; inspect the nonsecret plan and service state") from None


if __name__ == "__main__":
    main()
