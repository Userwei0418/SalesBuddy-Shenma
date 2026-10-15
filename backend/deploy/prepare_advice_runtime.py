"""Merge published advice metadata without opening credential files.

Run under operator-owned systemd EnvironmentFiles while the deployment lock is
held and API/worker are stopped. Keys are only checked for presence in Settings.
The existing pilot file is the canonical rollout source; unrelated entries are
preserved. A verified metadata backup is retained for operator recovery.
"""

import argparse
import json
import os
import pwd
import shlex
from pathlib import Path

from sales_backend.config import get_settings
from sales_backend.services.agent_platform.inference import binding_for


def validate_rollout_bindings(bindings, pilot):
    """An enabled rollout must not silently become direct-only after deployment."""
    raw = json.dumps(bindings, separators=(",", ":"))
    for workspace, configuration in pilot.items():
        capabilities = dict(configuration.get("capabilities", {}))
        if configuration.get("enabled") is True:
            capabilities["chatbi"] = configuration
        for capability, rollout in capabilities.items():
            if rollout.get("enabled") is True and binding_for(raw, workspace, capability) is None:
                raise ValueError("Enabled rollout has no valid binding: " + workspace + "/" + capability)


def restore_missing_bindings(current, recovered, workspace, capabilities):
    """Plan a scoped recovery from verified old metadata; never replace a binding.

    The caller supplies the explicitly reviewed capabilities and old bindings
    inherited from the existing service files. This function performs no I/O.
    """
    result = json.loads(json.dumps(current))
    selected = set(capabilities)
    if not selected:
        raise ValueError("Explicit recovery capabilities are required")
    raw = json.dumps(recovered, separators=(",", ":"))
    for capability in sorted(selected):
        if binding_for(raw, workspace, capability) is None:
            raise ValueError("Recovery source has no valid binding: " + capability)
        source = recovered[workspace][capability]
        existing = result.setdefault(workspace, {}).get(capability)
        if existing is not None and existing != source:
            raise ValueError("Recovery would replace an existing binding: " + capability)
        result[workspace][capability] = json.loads(json.dumps(source))
    return result


def merge_metadata(bindings, pilot, workspace, publication):
    bindings = json.loads(json.dumps(bindings))
    pilot = json.loads(json.dumps(pilot))
    caps = {"customer_advice", "opportunity_advice", "visit_advice"}
    assert set(publication) == caps
    for cap, item in publication.items():
        assert item["published"] and item["model"] == "senseaudio-s2" and not item["tools"]
        bindings.setdefault(workspace, {})[cap] = {
            "enabled": True, "execution_mode": "filtered_facts",
            "agent_id": item["id"], "expected_snapshot_id": item["snapshot_id"],
        }
        pilot.setdefault(workspace, {}).setdefault("capabilities", {})[cap] = {
            "enabled": True, "rollout": "production",
        }
    validate_rollout_bindings(bindings, pilot)
    return bindings, pilot


def atomic_write(path, text, mode, uid, gid):
    temporary = path.with_name(path.name + ".partial")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--publication", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--workspace", default="00000000-0000-0000-0000-000000000001")
    args = parser.parse_args()
    settings = get_settings()
    publication = json.loads(args.publication.read_text())
    for cap, item in publication.items():
        assert getattr(settings, "agent_fde_" + cap + "_id") == item["id"]
        assert getattr(settings, "agent_fde_" + cap + "_api_key")
    pilot_path = Path(settings.agent_fde_pilot_path)
    assert pilot_path.is_absolute() and pilot_path.is_file()
    original = pilot_path.read_text()
    bindings, pilot = merge_metadata(
        json.loads(settings.agent_platform_bindings_json), json.loads(original), args.workspace, publication,
    )
    target = Path("/etc/sales-fde-advice-bindings.env")
    assert not target.exists()
    backup = args.evidence / "pilot-before-advice.json"
    with backup.open("x") as stream:
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    owner = pwd.getpwnam("sales-backend")
    metadata = json.dumps(bindings, separators=(",", ":"))
    env = "AGENT_PLATFORM_BINDINGS_JSON=" + shlex.quote(metadata) + "\n"
    env += "AGENT_FDE_PILOT_PATH=" + shlex.quote(str(pilot_path)) + "\n"
    atomic_write(target, env, 0o640, 0, owner.pw_gid)
    stat = pilot_path.stat()
    atomic_write(pilot_path, json.dumps(pilot, indent=2) + "\n", stat.st_mode & 0o777, stat.st_uid, stat.st_gid)
    assert json.loads(pilot_path.read_text()) == pilot
    print(json.dumps({"configured": sorted(publication), "unrelated_capabilities_preserved": True,
                      "credentials_printed": False, "bindings_path": str(target)}))


if __name__ == "__main__":
    main()
