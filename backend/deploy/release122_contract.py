"""Exact V255 -> V268 1.2.2 admission, frozen from the reviewed candidate.

No execution authority or old-code rollback promise is provided by this module.
The existing forward orchestrator retains stop/backup/commit-outcome semantics.
"""

RELEASE_SCOPE = ["release_1.2.2"]
DEPENDENCIES = {
    "runtime_roles": "cf63fe593a4ffbe7b4a70bbb133bb2bae6d3562a887e80fc6f60dffa8152a76a",
    "upgrade_permissions": "dd8b8bb053ab3d38ae5b2c23f8fb65e547e6abcdf4ed413143abe2b248406d2f",
}
PENDING = ["V256", "V257", "V258", "V259", "V260", "V261", "V262", "V263", "V264", "V265", "V266", "V267", "V268"]
GATE_FILES = (
    "release122_contract.py",
    "release_forward_bundle.py",
    "release120_production_acl.py",
    "release_upgrade.py",
    "release_bundle_control.py",
)
BASE_LEDGER = "ad1e7af843327381b3740fec3fa9a91aab55d2ebbd31168e537372b0e395e971"
BASE_REGISTERED = "4de182750a0b54598a9e873b5663aabdadfbed1be3de24b7c97310b2babf050a"
TARGET_LEDGER = "8912e47bfad0ba4bf6c8ec840bd68963e68bfd4fb2fa98954a9328fa94789d9a"
TARGET_REGISTERED = "8e80ed5b22fd1997936bcbda38601b6d350a63d58df341415a034fb795515a13"
MIGRATION_PLAN_SHA = "bfef4bb3867ffe6a46c69c7075ac74f296a649d755163b2bd87c72eb2e411cb9"
SERVICES_SHA = "f53e7cd916002e7b6d5eabdf0d06a84fe10969c867d8531125c6706a8e464934"
POLICY_SHA = "fa6bdba5461a5b336ab252565d6fe8cf49225b3d3f252cb22f2d815eaad6c7ec"
SQL = {
    "V256": (
        "migrations/V256__opportunity_explicit_collaboration.sql",
        "5d8e9e11fbf61bb1cdf40d414335585ecf24ffd157e42c67057cf3706d5b9266",
    ),
    "V257": (
        "migrations/V257__task_execution_lifecycle.sql",
        "4e942c65d9037cc6733c2a1b28f116c0180d8e39d1185ae36318d4af7dc62ebb",
    ),
    "V258": (
        "migrations/V258__knowledge_materials.sql",
        "b090a351689a1c59e368b2b44f10a033d96c143cd9cb2479ad977baa063543c7",
    ),
    "V259": (
        "migrations/V259__opportunity_owner_transfer.sql",
        "d2e838ae32300217d89bef6bdc56958cb3556e1b4f62dc283cb87abd28635a0d",
    ),
    "V260": (
        "migrations/V260__opportunity_owner_follow_up.sql",
        "0a19506ba6e4c899da88643cbe202680155e2d7e766c1704fe5d4b15bccfd31e",
    ),
    "V261": (
        "migrations/V261__knowledge_acl_candidates.sql",
        "a4bfe2b9e5bbd1ad6d6f815bad7ac347e268bd7cf32f09dcf0c65e7289b82381",
    ),
    "V262": (
        "migrations/V262__task_execution_legacy_compat.sql",
        "408d8671f8a01b07b077ca9b2eda9a88c875b02f588a7c6b74152d8060dc2258",
    ),
    "V263": (
        "migrations/V263__task_reviewer_handover_requires_confirmation.sql",
        "faf0453026548b11941f89ebc71c75159df57d6f3f8332d280f2d02c4ea89a25",
    ),
    "V264": (
        "migrations/V264__knowledge_provider_bindings.sql",
        "ac2d290ce14965094fef9bf0f2749ef014bac51dfe9e931a25e8ae297dcc36c1",
    ),
    "V265": (
        "migrations/V265__knowledge_provider_binding_write.sql",
        "8d373a975e8876d7b04859782c6a93f09697709db3384f9c5b897923a796926f",
    ),
    "V266": (
        "migrations/V266__knowledge_work_sharing.sql",
        "97387eb4c4838ab6b4ad7274679995f7dd326b80e11615c93173584075f8cf9f",
    ),
    "V267": (
        "migrations/V267__knowledge_source_import.sql",
        "971b6745850b109d82bdc818e25ab6f6d036a4daa6c9a2abbcb8e6251af0b127",
    ),
    "V268": (
        "migrations/V268__knowledge_ingestion.sql",
        "7c97060d2680b7bb04d73ed436a0bd750afe68b250af91e374672dbfa5bd2eca",
    ),
}

def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    after = {row["key"]: row["sha256"] for row in migrations}
    require(
        target == "V268"
        and len(migrations) == len(after) == 227
        and digest(migrations) == MIGRATION_PLAN_SHA
        and digest(after) == TARGET_LEDGER,
        "1.2.2 requires the exact frozen 227-row migration plan",
    )
    registered = list(registered)
    require(len(registered) == len(set(registered)), "1.2.2 registered ledger has duplicates")
    if pending:
        require(
            source == "V255"
            and pending == PENDING
            and len(prior) == 214
            and digest(prior) == BASE_LEDGER
            and len(registered) == 245
            and digest(sorted(registered)) == BASE_REGISTERED
            and set(after) == set(prior) | set(PENDING)
            and all(after[key] == value for key, value in prior.items()),
            "1.2.2 requires unchanged complete V255 and only fixed V256-V268 pending",
        )
    else:
        require(
            source == "V268"
            and prior == after
            and len(registered) == 258
            and digest(sorted(registered)) == TARGET_REGISTERED,
            "1.2.2 final state requires exact V268 ledger and 258 registrations",
        )


def validate(plan, migrations, *, digest, require):
    require(
        plan.get("product_version") == "1.2.2"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan["pending"] == PENDING
        and plan.get("agent_stage") is None
        and plan.get("prepared_configuration_delta") is None
        and plan["expected_ledger"].get("running_jobs") == 0,
        "1.2.2 requires fixed scope, idle jobs and unchanged configuration",
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


def schema_contract(plan, *, digest, require):
    versions = plan.get("expected_registered_versions")
    require(
        plan.get("release_scope") == RELEASE_SCOPE
        and isinstance(versions, list)
        and len(versions) == 258
        and versions == sorted(set(versions))
        and digest(versions) == TARGET_REGISTERED,
        "1.2.2 ACL requires exact V268 registrations",
    )
    return "V268", dict(count=227, ledger=TARGET_LEDGER, registered=TARGET_REGISTERED)


def validate_acl(plan, policy, *, digest, require):
    schema_contract(plan, digest=digest, require=require)
    require(
        policy.get("schema") == "V268"
        and policy.get("migration_fingerprint") == TARGET_LEDGER
        and digest(policy.get("services")) == SERVICES_SHA
        and digest(policy) == POLICY_SHA,
        "1.2.2 ACL requires the exact reviewed V268 policy and service matrix",
    )
