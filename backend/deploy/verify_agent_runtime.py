"""Verify effective Agent metadata without printing credential values.

Run with inherit_service_environment.py for each service. Only binding and
rollout metadata are inspected; the report contains canonical digests so the
deployer can compare API/Worker and before/after configurations.
"""

import hashlib
import json
from pathlib import Path

from prepare_advice_runtime import validate_rollout_bindings

from sales_backend.config import get_settings


def metadata_object(raw):
    if not isinstance(raw, str) or len(raw) > 64000:
        raise ValueError("Agent metadata is missing or exceeds the runtime limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Agent metadata must be an object")
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify(settings):
    bindings = metadata_object(settings.agent_platform_bindings_json)
    pilot_source = settings.agent_fde_pilot_path
    if pilot_source:
        path = Path(pilot_source)
        if not path.is_absolute():
            raise ValueError("Canonical rollout metadata path must be absolute")
        with path.open(encoding="utf-8") as stream:
            raw = stream.read(64001)
    else:
        raw = settings.agent_fde_pilot_json
    pilot = metadata_object(raw)
    # Validate the entire rollout shape, including disabled entries, instead of
    # accepting malformed metadata that the runtime would silently ignore.
    for configuration in pilot.values():
        if not isinstance(configuration, dict):
            raise ValueError("Workspace rollout metadata must be an object")
        capabilities = configuration.get("capabilities", {})
        if not isinstance(capabilities, dict) or any(not isinstance(item, dict) for item in capabilities.values()):
            raise ValueError("Capability rollout metadata must contain objects")
    validate_rollout_bindings(bindings, pilot)
    return {
        "schema_version": 1,
        "bindings_sha256": digest(bindings),
        "rollout_sha256": digest(pilot),
        "rollout_source_sha256": digest(pilot_source or "inline"),
    }


def main():
    try:
        report = verify(get_settings())
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        # Avoid echoing file contents, Settings, JSON or credential values from
        # an exception. Operator-owned files can be inspected separately.
        raise SystemExit("Agent runtime metadata is invalid or an enabled rollout has no valid binding") from None
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
