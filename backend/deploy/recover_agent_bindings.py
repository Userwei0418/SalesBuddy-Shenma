"""Apply a reviewed, additive Agent binding recovery under the deployment lock.

Run through inherit_service_environment.py. Credential files are never opened;
Settings supplies only the binding metadata used below. The caller stops both
services, holds the deployment lock, and restarts/verifies them afterwards.
"""

import argparse
import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path

from prepare_advice_runtime import atomic_write, restore_missing_bindings, validate_rollout_bindings
from sales_backend.config import get_settings


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_plan(current, source, pilot, plan):
    if plan.get("schema_version") != 1 or digest(source) != plan["source_sha256"]:
        raise ValueError("Recovery source differs from reviewed metadata")
    selected = [row["capability"] for row in plan["changes"]]
    if len(set(selected)) != len(selected):
        raise ValueError("Repeated recovery capability")
    proposed = restore_missing_bindings(current, source, plan["workspace_id"], selected)
    if proposed != plan["proposed_bindings"] or digest(proposed) != plan["proposed_sha256"]:
        raise ValueError("Recovery would change unreviewed bindings")
    before = digest(current)
    if before not in {plan["current_sha256"], plan["proposed_sha256"]}:
        raise ValueError("Current bindings changed after review")
    validate_rollout_bindings(proposed, pilot)
    return proposed, before == plan["proposed_sha256"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    current = json.loads(settings.agent_platform_bindings_json)
    pilot_path = Path(settings.agent_fde_pilot_path)
    if not pilot_path.is_absolute() or not pilot_path.is_file():
        raise ValueError("Canonical rollout metadata is missing")
    pilot = json.loads(pilot_path.read_text())
    plan, source = json.loads(args.plan.read_text()), json.loads(args.source.read_text())
    proposed, unchanged = verify_plan(current, source, pilot, plan)
    report = {"applied": False, "already_applied": unchanged, "current_sha256": digest(current),
              "proposed_sha256": digest(proposed), "rollout_sha256": digest(pilot),
              "capabilities": sorted(row["capability"] for row in plan["changes"]),
              "credential_values_exported": False}
    if args.apply and not unchanged:
        if os.geteuid() != 0:
            raise ValueError("Recovery requires the deployment operator")
        for service in ("sales-api.service", "sales-worker.service"):
            state = subprocess.run(["systemctl", "show", service, "-p", "ActiveState", "--value"],
                                   check=True, capture_output=True, text=True).stdout.strip()
            if state != "inactive":
                raise ValueError("Stop API and Worker before applying recovery")
        target = Path("/etc/sales-fde-advice-bindings.env")
        if target.is_symlink() or not target.is_file():
            raise ValueError("Expected generated binding metadata file is missing")
        args.evidence.mkdir(mode=0o700, parents=False, exist_ok=False)
        stat = target.stat()
        # This backup contains only the typed nonsecret metadata, never API keys.
        atomic_write(args.evidence / "bindings-before.json", json.dumps(current, indent=2) + "\n", 0o600, 0, 0)
        atomic_write(args.evidence / "rollout-before.json", json.dumps(pilot, indent=2) + "\n", 0o600, 0, 0)
        value = "AGENT_PLATFORM_BINDINGS_JSON=" + shlex.quote(json.dumps(proposed, separators=(",", ":"))) + "\n"
        value += "AGENT_FDE_PILOT_PATH=" + shlex.quote(str(pilot_path)) + "\n"
        atomic_write(target, value, stat.st_mode & 0o777, stat.st_uid, stat.st_gid)
        if digest(json.loads(pilot_path.read_text())) != report["rollout_sha256"]:
            raise ValueError("Rollout changed concurrently; operator verification required")
        report["applied"] = True
        atomic_write(args.evidence / "result.json", json.dumps(report, indent=2) + "\n", 0o600, 0, 0)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
