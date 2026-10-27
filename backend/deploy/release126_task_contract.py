"""Exact V284 -> V286 task execution admission; no installation-matrix replay."""

RELEASE_SCOPE = ["release_1.2.6_task_execution"]
PENDING = ["V285", "V286"]
BASE_LEDGER = "4238eb37ecea62a0e8c3c43aa73572b1a5345f688ba703b0247cbffeed19873f"
BASE_REGISTERED = "84b694c2896a74ea714aa4b66c1df8a012ad186f5f9b8cd229fd017cdec62d30"
BASE_COMPLETE_LEDGER = "37ed293f51a96bb710800f0a5bcce311f423e9f0f31e80697264170bb46b63ac"
TARGET_LEDGER = "09ed2ae6eb80419381848f8c20b4a858b1583b114f8bae6ff5f80924fbe33975"
TARGET_REGISTERED = "8aa85e8c973e2e3af8f28371f6c9ae59cc5a21aa06f53c9c5b13ae8de9b03122"
TARGET_COMPLETE_LEDGER = "aa0a4555eb80001067217eed5ea8efcdf7dd822265fa096e475345d7b3c3f0a4"
MIGRATION_PLAN_SHA = "8f4699078f53e97c4841dbff8df12af305b87b457c09d66a85a53effed9dd88f"
INSTALLATION_POLICY_SHA = "c8255e968253c6cd7ab60dab8f3a430b319bb98c780db50ed11c79c3d653c37e"
SQL = {'V285': ('migrations/V285__task_execution_autonomous_checklist.sql', '306751aff39e52ce81782861905d610e5c2861ca63b2fee57d8da8240e944084'), 'V286': ('migrations/V286__task_pending_handover_eligibility.sql', '74bfa34c599c5ba9ebbc7bc483fc7723c436a912c6d55e7f7c498242602f13dd')}
GATE_FILES = (
    "release126_task_contract.py",
    "release126_task_security.py",
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
    "1.2.5",
    "1.2.5+mini.23a4d67fac911b10946620a3d87c888d415c56e9",
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
    "constraints",
    "triggers",
}


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    after = {row["key"]: row["sha256"] for row in migrations}
    require(
        target == "V286"
        and len(migrations) == len(after) == 245
        and digest(migrations) == MIGRATION_PLAN_SHA
        and digest(after) == TARGET_LEDGER
        and all(next((row for row in migrations if row["key"] == key), None)
                    == {"key": key, "file": value[0], "sha256": value[1]}
                for key, value in SQL.items()),
        "Task V2 requires the exact frozen 245-row migration plan",
    )
    registered = list(registered)
    require(len(registered) == len(set(registered)), "Duplicate schema registrations")
    complete = {"registered": sorted(registered), "checksums": prior}
    if pending:
        require(
            source == "V284"
            and pending == PENDING
            and len(prior) == 243
            and digest(prior) == BASE_LEDGER
            and len(registered) == 274
            and digest(sorted(registered)) == BASE_REGISTERED
            and digest(complete) == BASE_COMPLETE_LEDGER
            and set(after) == set(prior) | set(PENDING)
            and all(after[key] == value for key, value in prior.items()),
            "Task V2 requires unchanged complete V284 and only V285-V286",
        )
    else:
        require(
            source == "V286"
            and prior == after
            and len(registered) == 276
            and digest(sorted(registered)) == TARGET_REGISTERED
            and digest(complete) == TARGET_COMPLETE_LEDGER,
            "V286 final complete migration ledger differs",
        )


def validate(plan, migrations, *, digest, require):
    require(
        plan.get("product_version") == "1.2.6"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan.get("pending") == PENDING
        and plan.get("agent_stage") is None
        and plan.get("prepared_configuration_delta") is None
        and plan["expected_ledger"].get("running_jobs") == 0
        and plan.get("expected_database")
        == {"name": "sales_saas", "schema": "V284", "ledger_sha256": BASE_COMPLETE_LEDGER},
        "Task V2 requires complete V284, idle jobs and unchanged configuration",
    )
    revision = plan.get("candidate", {}).get("backend", {}).get("revision")
    mini_revision = plan.get("mini_source_revision")
    require(_hex(revision, 40) and _hex(mini_revision, 40),
            "Task V2 requires exact backend and Mini source revisions")
    active, supported = plan.get("active_mini_versions"), plan.get("supported_mini_versions")
    require(
        active == ACTIVE_MINI_VERSIONS
        and isinstance(supported, list)
        and all(isinstance(v, str) for v in supported)
        and len(supported) == len(set(supported))
        and set(supported) == set(ACTIVE_MINI_VERSIONS) | {"1.2.6", "1.2.6+mini." + mini_revision},
        "Task V2 requires all eighteen frozen clients plus 1.2.6 and its exact candidate identity",
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
        plan.get("format") == "salesbuddy-task-security-plan-v1"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan.get("mode") == "task_additive_runtime_grants"
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
        and len(versions) == 276
        and versions == sorted(set(versions))
        and digest(versions) == TARGET_REGISTERED,
        "Task V2 security plan must admit only the fixed additive grants at exact V286 registrations",
    )
    fingerprints = snapshot.get("fingerprints", {})
    require(
        snapshot.get("format") == "salesbuddy-live-security-v1"
        and set(fingerprints) == SECURITY_SECTIONS
        and all(_hex(value, 64) for value in fingerprints.values())
        and _hex(snapshot.get("index_fingerprint"), 64)
        and snapshot.get("runtime_roles_nonprivileged") is True,
        "Task V2 requires complete nonprivileged security and index snapshots",
    )


def validate_restore_evidence(evidence, objects, *, digest, require):
    restored = evidence.get('restored_database', {})
    require(
        evidence.get('format') == 'salesbuddy-task-restore-rehearsal-v1'
        and evidence.get('status') == 'passed'
        and evidence.get('source_database') == {'database': 'sales_saas', 'oid': 18684}
        and isinstance(restored, dict) and set(restored) == {'database', 'oid'}
        and isinstance(restored['database'], str) and restored['database'] != 'sales_saas'
        and isinstance(restored['oid'], int) and restored['oid'] != 18684
        and _hex(evidence.get('source_backup_sha256'), 64)
        and evidence.get('restored_from_production_backup') is True
        and evidence.get('source_schema') == 'V284' and evidence.get('target_schema') == 'V286'
        and evidence.get('source_ledger_sha256') == BASE_COMPLETE_LEDGER
        and evidence.get('target_ledger_sha256') == TARGET_COMPLETE_LEDGER
        and evidence.get('migration_plan_sha256') == MIGRATION_PLAN_SHA
        and evidence.get('new_objects_sha256') == digest(objects)
        and evidence.get('security_preserved') is True
        and evidence.get('business_state_preserved') is True
        and evidence.get('effective_privileges_verified') is True,
        'Task release requires a matching independent production-backup restore rehearsal',
    )
