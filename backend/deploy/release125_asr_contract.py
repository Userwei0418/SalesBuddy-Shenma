"""Exact V281 -> V284 ASR recovery admission; no installation-matrix replay."""

RELEASE_SCOPE = ["release_1.2.5_asr_recovery"]
PENDING = ["V282", "V283", "V284"]
BASE_LEDGER = "864e51b0994ee4fbef1ce57a6dd4aa766a82c411bce6e8418ff59f4e44f6b859"
BASE_REGISTERED = "797bafa0b1e0c0f47f03937d5488803b5d47d7620c5b92a5e2354a7392a39226"
BASE_COMPLETE_LEDGER = "d054a3d31cad793d2686fb0a59b7aa2c1d3ba694755452d8c1769c55dde6fc2c"
TARGET_LEDGER = "4238eb37ecea62a0e8c3c43aa73572b1a5345f688ba703b0247cbffeed19873f"
TARGET_REGISTERED = "84b694c2896a74ea714aa4b66c1df8a012ad186f5f9b8cd229fd017cdec62d30"
TARGET_COMPLETE_LEDGER = "37ed293f51a96bb710800f0a5bcce311f423e9f0f31e80697264170bb46b63ac"
MIGRATION_PLAN_SHA = "826745f0a9db0c3b30790b7273e947ce06013def38ebdcba61bb075fe8343eb5"
INSTALLATION_POLICY_SHA = "1e5252767e4cf4200018016727d6e795f4e73d64f3be93aeebcc7796c1ce27c7"
SQL = {'V282': ('migrations/V282__company_asr_terminology_and_receipts.sql', '6d7d5fdf2c885948dde1b650ce8aa165aca398f7a0364fdb1e0c0033e5766d87'), 'V283': ('migrations/V283__private_visit_audio.sql', 'c5d412d6891244f461e59f5e62f593f127b492809bc9def6db4105bd0c8840cc'), 'V284': ('migrations/V284__asr_local_transport_capacity.sql', '1f52717789736e565a2683ec0f0016e10ec583d4046f964f479743dfa45bc96d')}
GATE_FILES = (
    "release125_asr_contract.py",
    "release125_asr_security.py",
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
    "1.2.4",
    "1.2.4+mini.b9a32ef20bc76d15d1436440f442beff3e259360",
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
    "modified_function_properties",
}


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    after = {row["key"]: row["sha256"] for row in migrations}
    require(
        target == "V284"
        and len(migrations) == len(after) == 243
        and digest(migrations) == MIGRATION_PLAN_SHA
        and digest(after) == TARGET_LEDGER
        and all(next((row for row in migrations if row["key"] == key), None)
                    == {"key": key, "file": value[0], "sha256": value[1]}
                for key, value in SQL.items()),
        "ASR requires the exact frozen 243-row migration plan",
    )
    registered = list(registered)
    require(len(registered) == len(set(registered)), "Duplicate schema registrations")
    complete = {"registered": sorted(registered), "checksums": prior}
    if pending:
        require(
            source == "V281"
            and pending == PENDING
            and len(prior) == 240
            and digest(prior) == BASE_LEDGER
            and len(registered) == 271
            and digest(sorted(registered)) == BASE_REGISTERED
            and digest(complete) == BASE_COMPLETE_LEDGER
            and set(after) == set(prior) | set(PENDING)
            and all(after[key] == value for key, value in prior.items()),
            "ASR requires unchanged complete V281 and only V282-V284",
        )
    else:
        require(
            source == "V284"
            and prior == after
            and len(registered) == 274
            and digest(sorted(registered)) == TARGET_REGISTERED
            and digest(complete) == TARGET_COMPLETE_LEDGER,
            "V284 final complete migration ledger differs",
        )


def validate(plan, migrations, *, digest, require):
    require(
        plan.get("product_version") == "1.2.5"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan.get("pending") == PENDING
        and plan.get("agent_stage") is None
        and plan.get("prepared_configuration_delta") is None
        and plan["expected_ledger"].get("running_jobs") == 0
        and plan.get("expected_database")
        == {"name": "sales_saas", "schema": "V281", "ledger_sha256": BASE_COMPLETE_LEDGER},
        "ASR requires complete V281, idle jobs and unchanged configuration",
    )
    revision = plan.get("candidate", {}).get("backend", {}).get("revision")
    mini_revision = plan.get("mini_source_revision")
    require(_hex(revision, 40) and _hex(mini_revision, 40),
            "ASR requires exact backend and Mini source revisions")
    active, supported = plan.get("active_mini_versions"), plan.get("supported_mini_versions")
    require(
        active == ACTIVE_MINI_VERSIONS
        and isinstance(supported, list)
        and all(isinstance(v, str) for v in supported)
        and len(supported) == len(set(supported))
        and set(supported) == set(ACTIVE_MINI_VERSIONS) | {"1.2.5", "1.2.5+mini." + mini_revision},
        "ASR requires all sixteen frozen clients plus 1.2.5 and its exact candidate identity",
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
    """Freeze preserved catalogs; separately freeze new object definitions from restore rehearsal."""
    versions = plan.get("expected_registered_versions")
    require(
        plan.get("format") == "salesbuddy-asr-security-plan-v1"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan.get("mode") == "asr_additive_runtime_grants"
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
        and len(versions) == 274
        and versions == sorted(set(versions))
        and digest(versions) == TARGET_REGISTERED,
        "ASR security plan must admit only the fixed additive grants at exact V284 registrations",
    )
    fingerprints = snapshot.get("fingerprints", {})
    require(
        snapshot.get("format") == "salesbuddy-live-security-v1"
        and set(fingerprints) == SECURITY_SECTIONS
        and all(_hex(value, 64) for value in fingerprints.values())
        and _hex(snapshot.get("index_fingerprint"), 64)
        and snapshot.get("runtime_roles_nonprivileged") is True,
        "ASR requires complete nonprivileged security and index snapshots",
    )
