"""Exact V277 -> V278 knowledge silent-publication migration admission.

V278 records an explicit silent publication choice in the immutable audit and
keeps its Feishu event historical, without a notification snapshot. It reuses the reviewed V277 runtime ACL policy and
adds no provider, Web, Mini Program, or permission-scope change.
"""

RELEASE_SCOPE = ["release_1.2.2_knowledge_silent_publication"]
DEPENDENCIES = {
    "runtime_roles": "cf63fe593a4ffbe7b4a70bbb133bb2bae6d3562a887e80fc6f60dffa8152a76a",
    "upgrade_permissions": "dd8b8bb053ab3d38ae5b2c23f8fb65e547e6abcdf4ed413143abe2b248406d2f",
}
PENDING = ["V278"]
GATE_FILES = (
    "release122_knowledge_silent_publication_contract.py",
    "release_forward_bundle.py",
    "release120_production_acl.py",
    "release_upgrade.py",
    "release_bundle_control.py",
)
BASE_LEDGER = "a74aa740c988e3e1b6f90b0f9764530c57703fa8c90e8b0aad226d5ea6b39e13"
BASE_REGISTERED = "586fa574cf7959cdcbc97ce5bc7ab964c87d32ab7b652a67919d25b8f8fcd1ba"
TARGET_LEDGER = "c5e8c1d2a69be556b4efece07ffc352588e35473af32e81272f0af4e534b237e"
TARGET_REGISTERED = "440f6a9b1b2be638555b7732123e4081b303dfcd6098e96bf831cdd1d09c56fe"
MIGRATION_PLAN_SHA = "b76bcf73e50e0a56b5fedf51c78e3a547714b11928481a7bc766b550b0663feb"
SERVICES_SHA = "d3243ba38f3f378bcd29351958bd499ed2bcec30ec4a145f89484e57ed00d854"
POLICY_SHA = "d5a768bcd93782455085baf556a7f3effdc1ac722960d247fe5c0f8ea8245e01"
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
    "V278": (
        "migrations/V278__knowledge_silent_publication.sql",
        "fcb26b5bde3ee0ee0ff0b5f3ef70649189455c80463b975dffdb3a748833d07b",
    ),
}


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    after = {row["key"]: row["sha256"] for row in migrations}
    require(
        target == "V278"
        and len(migrations) == len(after) == 237
        and digest(migrations) == MIGRATION_PLAN_SHA
        and digest(after) == TARGET_LEDGER,
        "1.2.2 knowledge silent publication requires the exact frozen 237-row migration plan",
    )
    registered = list(registered)
    require(
        len(registered) == len(set(registered)), "1.2.2 knowledge silent publication registered ledger has duplicates"
    )
    if pending:
        require(
            source == "V277"
            and pending == PENDING
            and len(prior) == 236
            and digest(prior) == BASE_LEDGER
            and len(registered) == 267
            and digest(sorted(registered)) == BASE_REGISTERED
            and set(after) == set(prior) | set(PENDING)
            and all(after[key] == value for key, value in prior.items()),
            "1.2.2 knowledge silent publication requires unchanged complete V277 and only V278 pending",
        )
    else:
        require(
            source == "V278"
            and prior == after
            and len(registered) == 268
            and digest(sorted(registered)) == TARGET_REGISTERED,
            "1.2.2 knowledge silent publication final state requires exact V278 ledger and 268 registrations",
        )


def validate(plan, migrations, *, digest, require):
    require(
        plan.get("product_version") == "1.2.2"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan["pending"] == PENDING
        and plan.get("agent_stage") is None
        and plan.get("prepared_configuration_delta") is None
        and plan["expected_ledger"].get("running_jobs") == 0,
        "1.2.2 knowledge silent publication requires fixed scope, idle jobs and unchanged configuration",
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
        "1.2.2 knowledge silent publication requires all ten frozen active client registrations",
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
        and len(versions) == 268
        and versions == sorted(set(versions))
        and digest(versions) == TARGET_REGISTERED,
        "1.2.2 knowledge silent publication ACL requires exact V278 registrations",
    )
    return "V278", dict(count=237, ledger=TARGET_LEDGER, registered=TARGET_REGISTERED)


def validate_acl(plan, policy, *, digest, require):
    schema_contract(plan, digest=digest, require=require)
    require(
        policy.get("schema") == "V278"
        and policy.get("migration_fingerprint") == TARGET_LEDGER
        and digest(policy.get("services")) == SERVICES_SHA
        and digest(policy) == POLICY_SHA,
        "1.2.2 knowledge silent publication ACL requires V277 policy with V278 schema and ledger",
    )
