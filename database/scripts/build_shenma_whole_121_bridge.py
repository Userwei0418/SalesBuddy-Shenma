#!/usr/bin/env python3
"""Materialize the reviewed Shenma database bridge from an exact upstream tree.

Never renumber or rewrite an applied customer migration. Input must be the
`database` tree exported by SOURCE_REVISION. Manifest pins every upstream byte;
--check verifies the checked-in materialization without Git/network access.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

SOURCE_REVISION = "e40a8029d94a03fc45448e00d536e5a5b9120831"
ROOT = Path(__file__).resolve().parents[1]
POLICY_SHA256 = "31d0a6b3e525cc7ca931cb6744954c793e5321834962d54f01e79906d36a0d4d"
MANIFEST = ROOT / "shenma_whole_121_manifest.json"
SERVICE_POLICY = ROOT / "tests/fixtures/upstream_e40_runtime_permissions.json"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def transform(version, text):
    changes = [
        "map ledger version by +148",
        "namespace upstream numbered reconcile wrappers",
    ]
    text = re.sub(
        r"\breconcile_runtime_grants_v(\d+)\b",
        r"reconcile_runtime_grants_internal_v\1",
        text,
    )
    text, count = re.subn(
        r"(VALUES\s*\(\s*)'V" + str(version) + r"'",
        rf"\g<1>'V{version + 148}'",
        text,
        flags=re.IGNORECASE,
    )
    if count > 1 or (count == 0 and "INSERT INTO ops.schema_migration" in text):
        raise ValueError(f"V{version}: unexpected ledger insertion shape, got {count}")
    if version == 204:
        # S2 already exists as immutable customer V160. Reuse its rows, stricter
        # approved/customer consistency constraint and withdrawal policy.
        text = text.replace(
            "CREATE TABLE crm.customer_creation_request (",
            "CREATE TABLE IF NOT EXISTS crm.customer_creation_request (",
            1,
        )
        text = text.replace(
            "CREATE UNIQUE INDEX customer_creation_one_pending",
            "CREATE UNIQUE INDEX IF NOT EXISTS customer_creation_one_pending",
            1,
        )
        text = text.replace(
            "CREATE INDEX customer_creation_queue",
            "CREATE INDEX IF NOT EXISTS customer_creation_queue",
            1,
        )
        for policy in (
            "creation_request_applicant_read",
            "creation_request_reviewer_read",
            "creation_request_applicant_insert",
            "creation_request_reviewer_update",
        ):
            text = text.replace(
                f"CREATE POLICY {policy} ",
                f"DROP POLICY IF EXISTS {policy} ON crm.customer_creation_request;\nCREATE POLICY {policy} ",
                1,
            )
        # V160 already extended only unchanged stock roles. Do not re-grant
        # removed permissions to a customer's explicitly customized role.
        start = text.index(
            "INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)"
        )
        end = text.index("-- Match the runtime grant convention", start)
        text = (
            text[:start]
            + "-- Existing customer role grants were migrated by V160; retain their decisions.\n\n"
            + text[end:]
        )
        changes.append(
            "reuse V160 request table/indexes/policies and preserve customized role grants"
        )
    return text, changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-database", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    assert digest(SERVICE_POLICY.read_bytes()) == POLICY_SHA256, (
        "Pinned e40 policy changed"
    )
    if args.check:
        manifest = json.loads(MANIFEST.read_text())
        assert manifest["source_revision"] == SOURCE_REVISION
        policy = manifest["reviewed_service_policy"]
        assert digest((ROOT / policy["path"]).read_bytes()) == policy["sha256"]
        for entry in manifest["migrations"]:
            assert (
                digest((ROOT / entry["target"]).read_bytes()) == entry["target_sha256"]
            ), entry["target"]
        for entry in manifest["preserved_customer_migrations"] + manifest.get(
            "customer_adapters", []
        ):
            assert digest((ROOT / entry["path"]).read_bytes()) == entry["sha256"], (
                entry["path"]
            )
        print(
            json.dumps(
                {
                    "source_revision": SOURCE_REVISION,
                    "verified": len(manifest["migrations"]),
                    "preserved": len(manifest["preserved_customer_migrations"]),
                }
            )
        )
        return
    if not args.upstream_database:
        parser.error("--upstream-database is required to regenerate")
    upstream = args.upstream_database.resolve()
    previous = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else None
    known = (
        {item["source"]: item["source_sha256"] for item in previous["migrations"]}
        if previous
        else {}
    )
    if previous:
        for entry in previous["preserved_customer_migrations"]:
            if digest((ROOT / entry["path"]).read_bytes()) != entry["sha256"]:
                raise ValueError(
                    "Immutable customer history modified: " + entry["path"]
                )
    entries = []
    for path in sorted((upstream / "migrations").glob("V*.sql")):
        version = int(path.name[1:4])
        if not 152 <= version <= 250:
            continue
        raw = path.read_bytes()
        source = str(path.relative_to(upstream))
        if known and known.get(source) != digest(raw):
            raise ValueError(f"Upstream bytes changed from reviewed manifest: {source}")
        transformed, changes = transform(version, raw.decode())
        name = f"V{version + 148}__internal_{path.name.split('__', 1)[1]}"
        target = ROOT / "migrations" / name
        body = (
            f"-- Derived from internal {SOURCE_REVISION}, {source}.\n"
            f"-- Original SHA-256: {digest(raw)}. See shenma_whole_121_manifest.json.\n"
            + transformed
        ).encode()
        target.write_bytes(body)
        entries.append(
            {
                "source": source,
                "source_version": f"V{version}",
                "source_sha256": digest(raw),
                "target": str(target.relative_to(ROOT)),
                "target_version": f"V{version + 148}",
                "target_sha256": digest(body),
                "adaptations": changes,
            }
        )
    assert len(entries) == 92, len(entries)
    preserved = [
        {"path": str(path.relative_to(ROOT)), "sha256": digest(path.read_bytes())}
        for path in sorted(
            [*ROOT.glob("V*.sql"), *(ROOT / "migrations").glob("V*.sql")]
        )
        if int(path.name[1:4]) <= 161
    ]
    MANIFEST.write_text(
        json.dumps(
            {
                "source_revision": SOURCE_REVISION,
                "customer_history": "c991b693",
                "expected_schema": "V399",
                "mapping": "internal version + 148; upstream gaps intentionally retained",
                "reviewed_service_policy": {
                    "source_revision": SOURCE_REVISION,
                    "source_path": "tools/installation/runtime_permissions.json",
                    "path": str(SERVICE_POLICY.relative_to(ROOT)),
                    "sha256": digest(SERVICE_POLICY.read_bytes()),
                    "services": ["api", "worker"],
                    "intentional_exceptions": {
                        "crm.customer_creation_request": "No direct INSERT or table UPDATE; SELECT and UPDATE(reviewed_payload) only."
                    },
                },
                "preserved_customer_migrations": preserved,
                "customer_adapters": [
                    {
                        "path": str(path.relative_to(ROOT)),
                        "sha256": digest(path.read_bytes()),
                    }
                    for prefix in ("V299", "V399")
                    for path in (ROOT / "migrations").glob(prefix + "__*.sql")
                ],
                "migrations": entries,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(
        f"Generated {len(entries)} bridge migrations; preserved {len(preserved)} customer migration files."
    )


if __name__ == "__main__":
    main()
