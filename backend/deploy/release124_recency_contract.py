"""Exact V280 -> V281 admission for two bounded customer directory readers and API grants."""

RELEASE_SCOPE = ["release_1.2.4_follow_up_recency"]
PENDING = ["V281"]
BASE_LEDGER = "fbd9ad060e2dd1ce43bc6c46b7ac5edbd40e2cc82e62b58acefa9316e7983025"
BASE_REGISTERED = "04f58f9de87953d07f80a26c8a788a3c7713944df5754256fd4e45bf50c71590"
BASE_COMPLETE_LEDGER = "0a73c0bff1e5619f60a32a4746eb71ecfbeeb13cb5988034185198cbb284721a"
TARGET_LEDGER = "864e51b0994ee4fbef1ce57a6dd4aa766a82c411bce6e8418ff59f4e44f6b859"
TARGET_REGISTERED = "797bafa0b1e0c0f47f03937d5488803b5d47d7620c5b92a5e2354a7392a39226"
TARGET_COMPLETE_LEDGER = "d054a3d31cad793d2686fb0a59b7aa2c1d3ba694755452d8c1769c55dde6fc2c"
MIGRATION_PLAN_SHA = "ea29273f84d3d8328bef22affa607cda91ff31f111391eea93cdb8dcc4ffed8d"
INSTALLATION_POLICY_SHA = "5b6e0100ee21dfe8d9f28c3c5f624698529ce076118720aa95cd48d5e00f082b"
SQL = {
    "V281": (
        "migrations/V281__follow_up_customer_directory_recency.sql",
        "a16ae909dcc10bfcbee377d6de4d279977ff02aab6b643dd85ddb67f889b4fe9",
    )
}
GATE_FILES = (
    "release124_recency_contract.py",
    "release124_recency_security.py",
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
    "1.2.3",
    "1.2.3+mini.b5ffbdc03dd6bf877531bde488d0b55fab8d0d99",
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
        target == "V281"
        and len(migrations) == len(after) == 240
        and digest(migrations) == MIGRATION_PLAN_SHA
        and digest(after) == TARGET_LEDGER
        and next((row for row in migrations if row["key"] == "V281"), None)
        == {"key": "V281", "file": SQL["V281"][0], "sha256": SQL["V281"][1]},
        "V281 requires the exact frozen 240-row migration plan",
    )
    registered = list(registered)
    require(len(registered) == len(set(registered)), "Duplicate schema registrations")
    complete = {"registered": sorted(registered), "checksums": prior}
    if pending:
        require(
            source == "V280"
            and pending == PENDING
            and len(prior) == 239
            and digest(prior) == BASE_LEDGER
            and len(registered) == 270
            and digest(sorted(registered)) == BASE_REGISTERED
            and digest(complete) == BASE_COMPLETE_LEDGER
            and set(after) == set(prior) | set(PENDING)
            and all(after[key] == value for key, value in prior.items()),
            "V281 requires unchanged complete V280 and only the fixed V281 migration",
        )
    else:
        require(
            source == "V281"
            and prior == after
            and len(registered) == 271
            and digest(sorted(registered)) == TARGET_REGISTERED
            and digest(complete) == TARGET_COMPLETE_LEDGER,
            "V281 final complete migration ledger differs",
        )


def validate(plan, migrations, *, digest, require):
    require(
        plan.get("product_version") == "1.2.4"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan.get("pending") == PENDING
        and plan.get("agent_stage") is None
        and plan.get("prepared_configuration_delta") is None
        and plan["expected_ledger"].get("running_jobs") == 0
        and plan.get("expected_database")
        == {"name": "sales_saas", "schema": "V280", "ledger_sha256": BASE_COMPLETE_LEDGER},
        "V281 requires the fixed directory reader scope, complete V280, idle jobs and unchanged configuration",
    )
    revision = plan.get("candidate", {}).get("backend", {}).get("revision")
    mini_revision = plan.get("mini_source_revision")
    require(_hex(revision, 40) and _hex(mini_revision, 40),
            "V281 requires exact backend and Mini source revisions")
    active, supported = plan.get("active_mini_versions"), plan.get("supported_mini_versions")
    require(
        active == ACTIVE_MINI_VERSIONS
        and isinstance(supported, list)
        and all(isinstance(v, str) for v in supported)
        and len(supported) == len(set(supported))
        and set(supported) == set(ACTIVE_MINI_VERSIONS) | {"1.2.4", "1.2.4+mini." + mini_revision},
        "V281 requires all fourteen frozen clients plus 1.2.4 and its exact candidate identity",
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
    """Freeze the preserved catalog projection; the helper checks the two new readers."""
    versions = plan.get("expected_registered_versions")
    require(
        plan.get("format") == "salesbuddy-recency-security-plan-v1"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan.get("mode") == "follow_up_recency_api_execute_only"
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
        and len(versions) == 271
        and versions == sorted(set(versions))
        and digest(versions) == TARGET_REGISTERED,
        "V281 security plan must admit only the fixed two API reader grants at exact target registrations",
    )
    fingerprints = snapshot.get("fingerprints", {})
    require(
        snapshot.get("format") == "salesbuddy-live-security-v1"
        and set(fingerprints) == SECURITY_SECTIONS
        and all(_hex(value, 64) for value in fingerprints.values())
        and _hex(snapshot.get("index_fingerprint"), 64)
        and snapshot.get("runtime_roles_nonprivileged") is True,
        "V281 requires complete nonprivileged security and index snapshots",
    )
