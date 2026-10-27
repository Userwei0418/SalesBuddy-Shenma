"""Exact V279 -> V280 admission for one controlled FDE setter and API grant."""

RELEASE_SCOPE = ["release_1.2.3_fde_members_history"]
PENDING = ["V280"]
BASE_LEDGER = "c019b029de0243d5a0591e91c54087397788f416607aaf34f1b2ec61b1b14743"
BASE_REGISTERED = "55f969721893cc16cacae2550861f3a2f1af1107b06ceebaf7c4a96e1fbd4799"
BASE_COMPLETE_LEDGER = "81316ea7eff81021c97e0e627c5a4ad684bde0759dc6fa315abc6fdcd557a637"
TARGET_LEDGER = "fbd9ad060e2dd1ce43bc6c46b7ac5edbd40e2cc82e62b58acefa9316e7983025"
TARGET_REGISTERED = "04f58f9de87953d07f80a26c8a788a3c7713944df5754256fd4e45bf50c71590"
TARGET_COMPLETE_LEDGER = "0a73c0bff1e5619f60a32a4746eb71ecfbeeb13cb5988034185198cbb284721a"
MIGRATION_PLAN_SHA = "975ddd4594334517f7cb6cecaa638e556cc3e97d0af320562bbaa7ceee89a353"
INSTALLATION_POLICY_SHA = "0e2bab14d216618faeca65dc1bcd03a0fd7cc4a0313466b189bffad07d585034"
SQL = {
    "V280": (
        "migrations/V280__fde_membership_change_history.sql",
        "dbb59aedccc2b9c8dff2935cfbfa45d9c4f8399a5d0b8cec4488de714cba063d",
    )
}
GATE_FILES = (
    "release123_contract.py",
    "release123_fde_security.py",
    "release_forward_bundle.py",
    "release_upgrade.py",
    "release_bundle_control.py",
    "release_visit_index_security.py",
    "release122_visit_index_contract.py",
)
ACTIVE_MINI_VERSIONS = [
    "1.1.1",
    "1.1.2",
    "1.2.0",
    "1.2.1",
    "1.2.1+mini.084199244e3ab09cdf3e057a7751220e5f0b2ae7",
    "1.2.1+mini.89774df68d775dbf24055ae9aea5e8c69ec4ae62",
    "1.2.1+mini.a9b938ce25a11a3fedfac612a9b6682f9384337f",
    "1.2.1+mini.e1203ab110478d0edaea37aa29d484ef9ebd7451",
    "1.2.2",
    "1.2.2+mini.11964a8633739e8d36d21544c56b2e5832219d36",
    "1.2.2+mini.253724c80f2470a5b424c7e8397b65e7adf75b36",
    "1.2.2+mini.2917c1cec34688eb6998b91a5d57d15c3a5ad159",
]
SECURITY_SECTIONS = {
    "relations",
    "columns",
    "functions",
    "schemas",
    "roles",
    "memberships",
    "defaults",
    "policies",
    "database",
    "database_role_settings",
}


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    after = {row["key"]: row["sha256"] for row in migrations}
    require(
        target == "V280"
        and len(migrations) == len(after) == 239
        and digest(migrations) == MIGRATION_PLAN_SHA
        and digest(after) == TARGET_LEDGER
        and next((row for row in migrations if row["key"] == "V280"), None)
        == {"key": "V280", "file": SQL["V280"][0], "sha256": SQL["V280"][1]},
        "V280 requires the exact frozen 239-row migration plan",
    )
    registered = list(registered)
    require(len(registered) == len(set(registered)), "Duplicate schema registrations")
    complete = {"registered": sorted(registered), "checksums": prior}
    if pending:
        require(
            source == "V279"
            and pending == PENDING
            and len(prior) == 238
            and digest(prior) == BASE_LEDGER
            and len(registered) == 269
            and digest(sorted(registered)) == BASE_REGISTERED
            and digest(complete) == BASE_COMPLETE_LEDGER
            and set(after) == set(prior) | set(PENDING)
            and all(after[key] == value for key, value in prior.items()),
            "V280 requires unchanged complete V279 and only the fixed V280 migration",
        )
    else:
        require(
            source == "V280"
            and prior == after
            and len(registered) == 270
            and digest(sorted(registered)) == TARGET_REGISTERED
            and digest(complete) == TARGET_COMPLETE_LEDGER,
            "V280 final complete migration ledger differs",
        )


def validate(plan, migrations, *, digest, require):
    require(
        plan.get("product_version") == "1.2.3"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan.get("pending") == PENDING
        and plan.get("agent_stage") is None
        and plan.get("prepared_configuration_delta") is None
        and plan["expected_ledger"].get("running_jobs") == 0
        and plan.get("expected_database")
        == {"name": "sales_saas", "schema": "V279", "ledger_sha256": BASE_COMPLETE_LEDGER},
        "V280 requires the fixed FDE setter scope, complete V279, idle jobs and unchanged configuration",
    )
    revision = plan.get("candidate", {}).get("backend", {}).get("revision")
    require(_hex(revision, 40), "V280 requires the exact candidate source revision")
    active, supported = plan.get("active_mini_versions"), plan.get("supported_mini_versions")
    require(
        active == ACTIVE_MINI_VERSIONS
        and isinstance(supported, list)
        and all(isinstance(v, str) for v in supported)
        and len(supported) == len(set(supported))
        and set(supported) == set(ACTIVE_MINI_VERSIONS) | {"1.2.3", "1.2.3+mini." + revision},
        "V280 requires all twelve frozen clients plus 1.2.3 and its exact candidate identity",
    )
    validate_migrations(
        migrations,
        plan["expected_ledger"]["checksums"],
        plan["expected_ledger"]["registered"],
        plan["source_schema"],
        plan["target_schema"],
        plan["pending"],
        digest=digest,
        require=require,
    )


def validate_acl(plan, snapshot, *, digest, require):
    """Freeze the preserved catalog projection; the helper checks the new setter."""
    versions = plan.get("expected_registered_versions")
    require(
        plan.get("format") == "salesbuddy-fde-security-plan-v1"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan.get("mode") == "fde_history_api_execute_only"
        and plan.get("maintenance_owner") == "postgres"
        and plan.get("roles")
        == {
            "api": "sales_api_saas",
            "worker": "sales_worker_saas",
            "feishu": "sales_feishu_saas",
            "platform": "sales_platform_saas",
        }
        and plan.get("target") == {"database": "sales_saas", "oid": 18684}
        and isinstance(versions, list)
        and len(versions) == 270
        and versions == sorted(set(versions))
        and digest(versions) == TARGET_REGISTERED,
        "V280 security plan must admit only the fixed API setter grant at exact target registrations",
    )
    fingerprints = snapshot.get("fingerprints", {})
    require(
        snapshot.get("format") == "salesbuddy-live-security-v1"
        and set(fingerprints) == SECURITY_SECTIONS
        and all(_hex(value, 64) for value in fingerprints.values())
        and _hex(snapshot.get("index_fingerprint"), 64)
        and snapshot.get("runtime_roles_nonprivileged") is True,
        "V280 requires complete nonprivileged security and index snapshots",
    )
