"""Plan/apply the four reviewed legacy owner grants; never rewrite ownership.

Run with the candidate Python. Spec and plan must be private files. `plan` only
reads PostgreSQL; `apply` requires the exact reviewed plan hash and saves a
durable intent before its single transaction. Unknown commit is inspected, not
blindly retried. No credentials are printed or changed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/installation"))
from adopt_legacy_saas import validate_spec  # noqa: E402 - CLI adds the bundled installation module path.
from legacy_adoption_backup import connect  # noqa: E402
from legacy_adoption_state import (  # noqa: E402
    AdoptionError,
    atomic_json,
    fingerprint,
    locked,
    private_directory,
    read_private,
)
from legacy_definer_dependencies import apply_plan, build_plan  # noqa: E402
from runtime_roles import load_policy  # noqa: E402


async def execute(args):
    directory = private_directory(args.directory)
    spec = validate_spec(read_private(Path(args.spec)))
    policy = load_policy(Path(spec["new_release"]) / "tools/installation/runtime_permissions.json")
    with locked(directory):
        conn = await connect(spec)
        try:
            if args.action == "plan":
                async with conn.transaction(readonly=True):
                    plan = await build_plan(conn, spec["roles"], policy)
                atomic_json(directory / "PLAN.json", plan, replace=False)
                print("Plan saved; SHA256 " + fingerprint(plan) + "; grant count " + str(len(plan["grants"])))
                return
            plan = read_private(directory / "PLAN.json")
            if not args.plan_sha256 or fingerprint(plan) != args.plan_sha256:
                raise AdoptionError("Explicit reviewed plan SHA256 does not match")
            atomic_json(
                directory / "INTENT.json",
                {"plan_sha256": fingerprint(plan), "database_outcome": "unknown_until_receipt"},
                replace=False,
            )
            async with conn.transaction():
                await conn.execute("SET LOCAL lock_timeout='5s'")
                result = await apply_plan(conn, plan, spec["roles"], policy)
            atomic_json(directory / "RESULT.json", result, replace=False)
            print("Exact legacy definer dependencies repaired; runtime permissions verified")
        finally:
            await conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply"))
    parser.add_argument("--spec", required=True)
    parser.add_argument("--directory", required=True, help="Existing private directory dedicated to this repair")
    parser.add_argument("--plan-sha256")
    args = parser.parse_args()
    try:
        asyncio.run(execute(args))
    except Exception:
        print("Repair did not finish: retain plan/intent, inspect database outcome before retry", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
