"""Exact V276 -> V277 knowledge image-preview migration admission.

V277 only closes the database contract gap for signed PNG/JPEG/WebP previews in
public knowledge shares. It reuses the reviewed V276 runtime ACL policy and
adds no provider, Web, Mini Program, or permission-scope change.
"""

RELEASE_SCOPE = ["release_1.2.2_knowledge_image_preview"]
DEPENDENCIES = {
    "runtime_roles": "cf63fe593a4ffbe7b4a70bbb133bb2bae6d3562a887e80fc6f60dffa8152a76a",
    "upgrade_permissions": "dd8b8bb053ab3d38ae5b2c23f8fb65e547e6abcdf4ed413143abe2b248406d2f",
}
PENDING = ["V277"]
GATE_FILES = (
    "release122_knowledge_image_preview_contract.py",
    "release_forward_bundle.py",
    "release120_production_acl.py",
    "release_upgrade.py",
    "release_bundle_control.py",
)
BASE_LEDGER = "2b2ae70fae0fb39913fbd3de9a3918bd36a86c993afff7acd1095f9bc79dce12"
BASE_REGISTERED = "72d79759676e331941edb6f4719dc7ddd9d311c204946f14ce7147fbe3dbc78e"
TARGET_LEDGER = "a74aa740c988e3e1b6f90b0f9764530c57703fa8c90e8b0aad226d5ea6b39e13"
TARGET_REGISTERED = "586fa574cf7959cdcbc97ce5bc7ab964c87d32ab7b652a67919d25b8f8fcd1ba"
MIGRATION_PLAN_SHA = "a77b447aa1cafbf3cb1ba282475645bb5713aec79753d74ca19f70b204ad755c"
SERVICES_SHA = "d3243ba38f3f378bcd29351958bd499ed2bcec30ec4a145f89484e57ed00d854"
POLICY_SHA = "38f12b3f42990e92123b7421bdccad906bfd4e6d03f81e43e6f29d8beddc748a"
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
    "V277": (
        "migrations/V277__knowledge_image_preview.sql",
        "60224083fcbc9cd8218b3f7b48cf93820573c4f312a7e6e02c18d490bcc0a617",
    ),
}


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    after = {row["key"]: row["sha256"] for row in migrations}
    require(
        target == "V277"
        and len(migrations) == len(after) == 236
        and digest(migrations) == MIGRATION_PLAN_SHA
        and digest(after) == TARGET_LEDGER,
        "1.2.2 knowledge image preview requires the exact frozen 236-row migration plan",
    )
    registered = list(registered)
    require(len(registered) == len(set(registered)), "1.2.2 knowledge image preview registered ledger has duplicates")
    if pending:
        require(
            source == "V276"
            and pending == PENDING
            and len(prior) == 235
            and digest(prior) == BASE_LEDGER
            and len(registered) == 266
            and digest(sorted(registered)) == BASE_REGISTERED
            and set(after) == set(prior) | set(PENDING)
            and all(after[key] == value for key, value in prior.items()),
            "1.2.2 knowledge image preview requires unchanged complete V276 and only V277 pending",
        )
    else:
        require(
            source == "V277"
            and prior == after
            and len(registered) == 267
            and digest(sorted(registered)) == TARGET_REGISTERED,
            "1.2.2 knowledge image preview final state requires exact V277 ledger and 267 registrations",
        )


def validate(plan, migrations, *, digest, require):
    require(
        plan.get("product_version") == "1.2.2"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan["pending"] == PENDING
        and plan.get("agent_stage") is None
        and plan.get("prepared_configuration_delta") is None
        and plan["expected_ledger"].get("running_jobs") == 0,
        "1.2.2 knowledge image preview requires fixed scope, idle jobs and unchanged configuration",
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
        "1.2.2 knowledge image preview requires all ten frozen active client registrations",
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
        and len(versions) == 267
        and versions == sorted(set(versions))
        and digest(versions) == TARGET_REGISTERED,
        "1.2.2 knowledge image preview ACL requires exact V277 registrations",
    )
    return "V277", dict(count=236, ledger=TARGET_LEDGER, registered=TARGET_REGISTERED)


def validate_acl(plan, policy, *, digest, require):
    schema_contract(plan, digest=digest, require=require)
    require(
        policy.get("schema") == "V277"
        and policy.get("migration_fingerprint") == TARGET_LEDGER
        and digest(policy.get("services")) == SERVICES_SHA
        and digest(policy) == POLICY_SHA,
        "1.2.2 knowledge image preview ACL requires V276 policy with V277 schema and ledger",
    )
