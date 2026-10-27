"""Exact V275 -> V276 knowledge label migration admission.

This is intentionally a separate contract from the historical V268 -> V275
knowledge distribution release.  V276 only changes the Feishu source
projection labels; it still requires the complete V275 ledger and the same
runtime permission matrix.  Keeping the scope separate prevents a V275 plan
from being silently reused for a V276 candidate.
"""

RELEASE_SCOPE = ["release_1.2.2_knowledge_labels"]
DEPENDENCIES = {
    "runtime_roles": "cf63fe593a4ffbe7b4a70bbb133bb2bae6d3562a887e80fc6f60dffa8152a76a",
    "upgrade_permissions": "dd8b8bb053ab3d38ae5b2c23f8fb65e547e6abcdf4ed413143abe2b248406d2f",
}
PENDING = ["V276"]
GATE_FILES = (
    "release122_knowledge_labels_contract.py",
    "release_forward_bundle.py",
    "release120_production_acl.py",
    "release_upgrade.py",
    "release_bundle_control.py",
)
BASE_LEDGER = "b16c88f61c8f1d0aa2af70f2b368ef338c9dd2952ac3f2c28575753d2ac48284"
BASE_REGISTERED = "630f736cbe955027780fb27f7756347b5c9b27349f5b1ddd0068eea3b9fa90c4"
TARGET_LEDGER = "2b2ae70fae0fb39913fbd3de9a3918bd36a86c993afff7acd1095f9bc79dce12"
TARGET_REGISTERED = "72d79759676e331941edb6f4719dc7ddd9d311c204946f14ce7147fbe3dbc78e"
MIGRATION_PLAN_SHA = "675fe8c99e3d277ce03186b67d7cbac9cd458c2331519e82690e5f61b897be5f"
SERVICES_SHA = "d3243ba38f3f378bcd29351958bd499ed2bcec30ec4a145f89484e57ed00d854"
POLICY_SHA = "1911033fce87b21c82679aa141f7898b24fda4e4875f0f664302845d0f9d9239"
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
    "V276": (
        "migrations/V276__knowledge_feishu_labels.sql",
        "560b11b6810b6b809cb808f1d12bb987fcc4bdf6a9cc750d250c628dc8af09ce",
    ),
}


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    after = {row["key"]: row["sha256"] for row in migrations}
    require(
        target == "V276"
        and len(migrations) == len(after) == 235
        and digest(migrations) == MIGRATION_PLAN_SHA
        and digest(after) == TARGET_LEDGER,
        "1.2.2 knowledge labels requires the exact frozen 235-row migration plan",
    )
    registered = list(registered)
    require(len(registered) == len(set(registered)), "1.2.2 knowledge labels registered ledger has duplicates")
    if pending:
        require(
            source == "V275"
            and pending == PENDING
            and len(prior) == 234
            and digest(prior) == BASE_LEDGER
            and len(registered) == 265
            and digest(sorted(registered)) == BASE_REGISTERED
            and set(after) == set(prior) | set(PENDING)
            and all(after[key] == value for key, value in prior.items()),
            "1.2.2 knowledge labels requires unchanged complete V275 and only V276 pending",
        )
    else:
        require(
            source == "V276"
            and prior == after
            and len(registered) == 266
            and digest(sorted(registered)) == TARGET_REGISTERED,
            "1.2.2 knowledge labels final state requires exact V276 ledger and 266 registrations",
        )


def validate(plan, migrations, *, digest, require):
    require(
        plan.get("product_version") == "1.2.2"
        and plan.get("release_scope") == RELEASE_SCOPE
        and plan["pending"] == PENDING
        and plan.get("agent_stage") is None
        and plan.get("prepared_configuration_delta") is None
        and plan["expected_ledger"].get("running_jobs") == 0,
        "1.2.2 knowledge labels requires fixed scope, idle jobs and unchanged configuration",
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
        "1.2.2 knowledge labels requires all ten frozen active client registrations",
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
        and len(versions) == 266
        and versions == sorted(set(versions))
        and digest(versions) == TARGET_REGISTERED,
        "1.2.2 knowledge labels ACL requires exact V276 registrations",
    )
    return "V276", dict(count=235, ledger=TARGET_LEDGER, registered=TARGET_REGISTERED)


def validate_acl(plan, policy, *, digest, require):
    schema_contract(plan, digest=digest, require=require)
    require(
        policy.get("schema") == "V276"
        and policy.get("migration_fingerprint") == TARGET_LEDGER
        and digest(policy.get("services")) == SERVICES_SHA
        and digest(policy) == POLICY_SHA,
        "1.2.2 knowledge labels ACL requires the exact reviewed V276 policy and service matrix",
    )
