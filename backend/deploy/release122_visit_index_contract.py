"""Exact V278 -> V279 admission; preserve live privileges, never replay ACLs."""

RELEASE_SCOPE = ["release_1.2.2_visit_field_prefix_index"]
PENDING = ["V279"]
BASE_LEDGER = "c5e8c1d2a69be556b4efece07ffc352588e35473af32e81272f0af4e534b237e"
BASE_REGISTERED = "440f6a9b1b2be638555b7732123e4081b303dfcd6098e96bf831cdd1d09c56fe"
TARGET_LEDGER = "c019b029de0243d5a0591e91c54087397788f416607aaf34f1b2ec61b1b14743"
TARGET_REGISTERED = "55f969721893cc16cacae2550861f3a2f1af1107b06ceebaf7c4a96e1fbd4799"
MIGRATION_PLAN_SHA = "fcce636bf5578aaa77e381a80d718e19a8d16697d50f77ecc32a7f4fec6af205"
INSTALLATION_POLICY_SHA = "d9d8ab8f9089c1395d2b50639ef0c9f94e6fd10c667b47ceb7bfab369206099f"
SQL = {
    "V279": (
        "migrations/V279__visit_field_value_prefix_index.sql",
        "ae79f120bdd836e0628da95073eca304ce445cdbf84a11ba9368311b96567677",
    )
}
GATE_FILES = (
    "release122_visit_index_contract.py",
    "release_visit_index_security.py",
    "release_forward_bundle.py",
    "release_upgrade.py",
    "release_bundle_control.py",
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


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    after = {row["key"]: row["sha256"] for row in migrations}
    require(
        target == "V279"
        and len(migrations) == len(after) == 238
        and digest(migrations) == MIGRATION_PLAN_SHA
        and digest(after) == TARGET_LEDGER,
        "V279 requires the exact frozen 238-row migration plan",
    )
    registered = list(registered)
    require(len(registered) == len(set(registered)), "Duplicate schema registrations")
    if pending:
        require(
            source == "V278"
            and pending == PENDING
            and len(prior) == 237
            and digest(prior) == BASE_LEDGER
            and len(registered) == 268
            and digest(sorted(registered)) == BASE_REGISTERED
            and set(after) == set(prior) | set(PENDING)
            and all(after[key] == value for key, value in prior.items()),
            "V279 requires unchanged complete V278 and only the fixed V279 migration",
        )
    else:
        require(
            source == "V279"
            and prior == after
            and len(registered) == 269
            and digest(sorted(registered)) == TARGET_REGISTERED,
            "V279 final complete migration ledger differs",
        )


def validate(plan, migrations, *, digest, require):
    require(
        plan.get("product_version") == "1.2.2"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan.get("pending") == PENDING
        and plan.get("agent_stage") is None
        and plan.get("prepared_configuration_delta") is None
        and plan["expected_ledger"].get("running_jobs") == 0,
        "V279 requires index-only scope, idle jobs and unchanged configuration",
    )
    active, supported = plan.get("active_mini_versions"), plan.get("supported_mini_versions")
    require(
        active == ACTIVE_MINI_VERSIONS
        and isinstance(supported, list)
        and all(isinstance(v, str) for v in supported)
        and len(supported) == len(set(supported))
        and set(active) <= set(supported),
        "V279 requires all eleven frozen active client identities",
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
    """A fixed live-catalog snapshot replaces installation-policy reconciliation."""
    versions = plan.get("expected_registered_versions")
    require(
        plan.get("release_scope") == RELEASE_SCOPE
        and plan.get("mode") == "preserve"
        and isinstance(versions, list)
        and len(versions) == 269
        and versions == sorted(set(versions))
        and digest(versions) == TARGET_REGISTERED,
        "V279 security plan must preserve privileges at exact target registrations",
    )
    fingerprints = snapshot.get("fingerprints", {})
    require(
        snapshot.get("format") == "salesbuddy-live-security-v1"
        and set(fingerprints) == SECURITY_SECTIONS
        and all(
            isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v)
            for v in fingerprints.values()
        )
        and snapshot.get("runtime_roles_nonprivileged") is True,
        "V279 requires a complete nonprivileged live security snapshot",
    )
