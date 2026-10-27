"""Exact V268 -> V275 knowledge distribution admission.

The historical V255 -> V268 release_1.2.2 contract remains independent.
Full prior registrations come from its reviewed frozen target, not a new-install
ledger, which omits retained pre-baseline registrations. No production plan,
execution authority or old-code rollback promise is supplied here.
"""

RELEASE_SCOPE = ["release_1.2.2_knowledge_distribution"]
DEPENDENCIES = {
    "runtime_roles": "cf63fe593a4ffbe7b4a70bbb133bb2bae6d3562a887e80fc6f60dffa8152a76a",
    "upgrade_permissions": "dd8b8bb053ab3d38ae5b2c23f8fb65e547e6abcdf4ed413143abe2b248406d2f",
}
PENDING = ["V269", "V270", "V271", "V272", "V273", "V274", "V275"]
GATE_FILES = (
    "release122_knowledge_contract.py",
    "release_forward_bundle.py",
    "release120_production_acl.py",
    "release_upgrade.py",
    "release_bundle_control.py",
)
BASE_LEDGER = "8912e47bfad0ba4bf6c8ec840bd68963e68bfd4fb2fa98954a9328fa94789d9a"
BASE_REGISTERED = "8e80ed5b22fd1997936bcbda38601b6d350a63d58df341415a034fb795515a13"
TARGET_LEDGER = "b16c88f61c8f1d0aa2af70f2b368ef338c9dd2952ac3f2c28575753d2ac48284"
TARGET_REGISTERED = "630f736cbe955027780fb27f7756347b5c9b27349f5b1ddd0068eea3b9fa90c4"
MIGRATION_PLAN_SHA = "575efdb40439577472d8badfbeab51f6441b914db7a4db045b56498374020dca"
SERVICES_SHA = "d3243ba38f3f378bcd29351958bd499ed2bcec30ec4a145f89484e57ed00d854"
POLICY_SHA = "1cf03197d32eeeb53fc1ae3b20a92f9382e2b46cea4a3257fbde3d858286d2e3"
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
]
SQL = {
    "V269": (
        "migrations/V269__knowledge_customer_distribution.sql",
        "dc546fc5e349dad153e161b15aecca740ddfc62b6d1d83a1e94efe2fb0a4e4ab",
    ),
    "V270": (
        "migrations/V270__knowledge_distribution_feishu.sql",
        "52f323a648ae36e5264646aae893545484ebed7df10922796541fbf5744a80be",
    ),
    "V271": (
        "migrations/V271__knowledge_distribution_access_boundaries.sql",
        "1636c6a1215b9588e877055c4e5e9bc6617229fbe5a8023cf5d984482845392f",
    ),
    "V272": (
        "migrations/V272__knowledge_notification_intent.sql",
        "e88379687dd76cda0d2b6b4b04c6e5269e101f65e54aa8b2081819dee426f96b",
    ),
    "V273": (
        "migrations/V273__knowledge_pdf_preview.sql",
        "cbb823b0e0db63d44786a9f7abbfe5f1838c950e3f1a06df7ddc94a0b757951c",
    ),
    "V274": (
        "migrations/V274__knowledge_source_file_archive.sql",
        "787d1e19e04cc96a2d7553e585e08c27b2e9205e5622c7ebfee2b10aba408eb1",
    ),
    "V275": (
        "migrations/V275__opportunity_collaborator_original_access.sql",
        "155162fe3b2db3046f54556393d9b8a4e28fe3991b464ca6cbb3a28661c4d3a2",
    ),
}


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    after = {row["key"]: row["sha256"] for row in migrations}
    require(
        target == "V275"
        and len(migrations) == len(after) == 234
        and digest(migrations) == MIGRATION_PLAN_SHA
        and digest(after) == TARGET_LEDGER,
        "1.2.2 knowledge requires the exact frozen 234-row migration plan",
    )
    registered = list(registered)
    require(len(registered) == len(set(registered)), "1.2.2 knowledge registered ledger has duplicates")
    if pending:
        require(
            source == "V268"
            and pending == PENDING
            and len(prior) == 227
            and digest(prior) == BASE_LEDGER
            and len(registered) == 258
            and digest(sorted(registered)) == BASE_REGISTERED
            and set(after) == set(prior) | set(PENDING)
            and all(after[key] == value for key, value in prior.items()),
            "1.2.2 knowledge requires unchanged complete V268 and only fixed V269-V275 pending",
        )
    else:
        require(
            source == "V275"
            and prior == after
            and len(registered) == 265
            and digest(sorted(registered)) == TARGET_REGISTERED,
            "1.2.2 knowledge final state requires exact V275 ledger and 265 registrations",
        )


def validate(plan, migrations, *, digest, require):
    require(
        plan.get("product_version") == "1.2.2"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan["pending"] == PENDING
        and plan.get("agent_stage") is None
        and plan.get("prepared_configuration_delta") is None
        and plan["expected_ledger"].get("running_jobs") == 0,
        "1.2.2 knowledge requires fixed scope, idle jobs and unchanged configuration",
    )
    active = plan.get("active_mini_versions")
    supported = plan.get("supported_mini_versions")
    require(
        isinstance(active, list)
        and active == ACTIVE_MINI_VERSIONS
        and isinstance(supported, list)
        and all(isinstance(v, str) for v in supported)
        and len(supported) == len(set(supported))
        and set(active) <= set(supported),
        "1.2.2 knowledge requires all ten frozen active client registrations",
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
        and len(versions) == 265
        and versions == sorted(set(versions))
        and digest(versions) == TARGET_REGISTERED,
        "1.2.2 knowledge ACL requires exact V275 registrations",
    )
    return "V275", dict(count=234, ledger=TARGET_LEDGER, registered=TARGET_REGISTERED)


def validate_acl(plan, policy, *, digest, require):
    schema_contract(plan, digest=digest, require=require)
    require(
        policy.get("schema") == "V275"
        and policy.get("migration_fingerprint") == TARGET_LEDGER
        and digest(policy.get("services")) == SERVICES_SHA
        and digest(policy) == POLICY_SHA,
        "1.2.2 knowledge ACL requires the exact reviewed V275 policy and service matrix",
    )
