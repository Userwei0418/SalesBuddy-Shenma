"""Finite, reviewed Review/OKR production increments above the V239 ledger.

An unfrozen stage is deliberately inadmissible. Each stage is frozen only after
Backend supplies its final SQL, complete ledger and exact runtime-role policy.
This contract grants no execution authority and never reads the database.
"""

from identity_release_contract import TARGET_LEDGER as BASE_LEDGER
from identity_release_contract import TARGET_REGISTERED as BASE_REGISTERED

RELEASE_SCOPE = ["review_okr_1.2.1"]
# This is the exact deployed V250 ledger, not a claim that V247-V249 ran.
# V251/V252 belong to a separate mainline upgrade and are deliberately absent.
HISTORY_SOURCE = {
    "count": 211,
    "ledger": "e705b47e56df842e0f13cb4d4e819b7e69cc6f5bb307fd8a8be842182650d3bf",
    "registered_count": 242,
    "registered": "005a3199c2268e75a7976842bc741022168a476dbb8efa2654a778c5954cfa54",
}
STAGES = {
    "V240": {
        "count": 204,
        "ledger": "a1e512a43c5eb8f201a3c1f3811e3db1781a793923ab5a19cf3dc1691a89b6e2",
        "registered_count": 235,
        "registered": "9d1796635f081b9cf68d30099e504bad8213a6686bbd96884b0da1e1bf79ce96",
        "sql": (
            "migrations/V240__review_metric_dictionary.sql",
            "58f4de04d5da4fb455bdbcb8d2e231079714eee5e044e1aae3b48725d084e4c2",
        ),
        "services": "68dd095b4150a75a890b8e491aed631fcd4f0dd3bc058c25bdd20a72ac09c7f3",
        "policy": "9e26677f2fb9bea322f3d690c533b8e43631a19cf32b2780dfca4d9bb1e2daf4",
    },
    "V241": {
        "count": 205,
        "ledger": "7f2fecd82c3cd78241e74de3633083985b4cf9c10337eafef1440d0b18739477",
        "registered_count": 236,
        "registered": "05c16c1bc36a7c2019867c7e629e481457541d33294f7499c5993e2150696684",
        "sql": (
            "migrations/V241__review_okr.sql",
            "a8384262a44721dd2f744761e2d5db7a5334a2575aff719c6bd9801420fe4327",
        ),
        "services": "9198ad7d314bef21bf6b37c02685670da2caa6a16aaea302017fdc42d17a94f4",
        "policy": "11a17e958f9aaf2f6113b4031e3dc05e3031da79e368a20f4e579a2a4bfe7b8d",
    },
    "V242": {
        "count": 206,
        "ledger": "6ab7a851eea498654204bfd780af5247738b661137796277059a59561687c08c",
        "registered_count": 237,
        "registered": "b30466cb17b58d9bf1904edd70a0a9a65a2ba48ebf097e2891a52de4bd14d569",
        "sql": (
            "migrations/V242__task_metric_alignment.sql",
            "431e9e5d89ff3d854c58f36e11bf14111142c35dbcf1b010db5719f981d01221",
        ),
        "services": "0fc30e384ac93ecebd5c1da4bb81a55d938ff0ed6755548748e090c8f6b6fd9b",
        "policy": "f9497f8498c6f8451a4446970be4280ddb3e13fdfb94b40eedfdeb1a7714948f",
    },
    "V243": {
        "count": 207,
        "ledger": "93cde594e52ac93e7bc6392d341efeb5b69b0dedd553abe2bd77794de3ce71e7",
        "registered_count": 238,
        "registered": "800bd1b4e97054bba279f90a4d4219ef1f78a9683468e3e7ec256fd0e4916dcf",
        "sql": (
            "migrations/V243__review_count_metric_definitions.sql",
            "8f6bbf86ec464a4792d5c1e9c056d24f31c6d911d3b4b8e2324cb20d72b43a53",
        ),
        "services": "0fc30e384ac93ecebd5c1da4bb81a55d938ff0ed6755548748e090c8f6b6fd9b",
        "policy": "4b8f5ad6fa1e80cfa7d1ea84a05a439f7e60e342b46401a6f21029bc56651ae7",
    },
    "V244": {
        "count": 208,
        "ledger": "bc695fc1788ed21db32b001fb91b7531ddbdf1a0080bd86195d3c03f468a2d7a",
        "registered_count": 239,
        "registered": "6b7c8a8b8a9b124366a393abed45ab30307a8e2347e813a5ebd11c2288593054",
        "sql": (
            "migrations/V244__task_alignment_object_basis.sql",
            "5f60cca782015a27a78274aff0872a3e92a82cbae2bfcf2f8f01543ffbb7ff42",
        ),
        "services": "0fc30e384ac93ecebd5c1da4bb81a55d938ff0ed6755548748e090c8f6b6fd9b",
        "policy": "0e10456890742e325543b34fc3bd68fad4ea88fff7b5e56f885be35a9d69b759",
    },
    "V245": {
        "count": 209,
        "ledger": "eb90275d89a84f96c5a7ea67c1eff3b39bee837d5ce0f64616a76e6dafbef082",
        "registered_count": 240,
        "registered": "056efce1f859c1e6eb865f5d8e9f6921b9efc05bf45abad918c22865b3e2f4c7",
        "sql": (
            "migrations/V245__work_report_review_freeze.sql",
            "36cab1055618b765dc822bdd69fa15f8b73e262316d31481969728adea06d19b",
        ),
        # V245 grants the review-access helper to the existing API/Worker
        # principals.  The policy is the normalized V244 candidate with the
        # V245 ledger and post-V244 RLS fingerprint bound to this stage.
        "services": "5210aaca64e37cb682357a29c2bcdb4f8f21380162a18a2a8242a182ace3f2c5",
        "policy": "4831e4e2fc96fe56f60a15d6a7a8810be7fa50a215b287b8da59a02ca4560b03",
    },
    "V246": {
        "count": 210,
        "ledger": "18955760424d87851ff4df2decb6c07cef9e978d6807c4575a902eeb3fa22a6e",
        "registered_count": 241,
        "registered": "94bdc85a3a103855b4d77bbc19a842419ba3674e3803e535b5f599cab0db9691",
        "sql": (
            "migrations/V246__review_worker_fact_reads.sql",
            "947f157f37bf721bef5b0e8bbae5e28cb223a4a82b07fb6db81c3a3f07611d33",
        ),
        # V246 adds only the five Worker fact-table reads and four helper
        # executes.  This policy is the normalized V245 candidate with the
        # complete V246 ledger and post-V244 RLS fingerprint bound to it.
        "services": "83c998a5dac7ef2b64b748ba0cbae7619f26c2b69774aa8a05c17e95e773a41e",
        "policy": "b62f675ccde31b5520051f8ad33de934d0db6b00a392368754c004faa5f2717d",
    },
    # Sparse, reviewed successor of the exact deployed V250 ledger. This does
    # not authorize the independent V251/V252 mainline migrations.
    "V253": {
        "count": 212,
        "ledger": "e2822093985fd15826ae024fd33b1d6ccf5fe56a5b5570c12d05e1872793c762",
        "registered_count": 243,
        "registered": "0f3fa71d1bfbc8a7d4366ddc071d2ec7e3783705b461baeb7684271720132cc9",
        "sql": (
            "migrations/V253__review_history_and_daily_baselines.sql",
            "1993064e95f27f59418abaeeb6345e8583d29e01895a1e4a640ecbcbaaf0f442",
        ),
        "services": "e03d9cb335881e389d5fc29557aea446cecf8545e223058f3cffda8ec3077ce0",
        "policy": "f2461bf61611433e05184c3e47146e51ec8b5bde29677b398cdf501e504674bc",
    },
    # V254 is a reviewed successor of the exact deployed V253 ledger. It adds
    # only the actor-scoped opportunity activity projection; no V251/V252
    # migrations or unrelated runtime-policy changes are admitted.
    "V254": {
        "count": 213,
        "ledger": "d1a333ef819183a1d5c4cc88c198306e9443a7e4b093dbcc408e67402a40ddb9",
        "registered_count": 244,
        "registered": "4fd6b0591f180110759fabee4c2a3a32400488b9f5218e0c0e9510f4082594e6",
        "sql": (
            "migrations/V254__review_update_activity.sql",
            "a4f17558bffcc142b39a3ee9da74f89b5d70d761fba1635491bfacb3c7489e7e",
        ),
        "services": "2fe0025cf798d9886a9959ac9e222c8058c08827e7dbc9a7436588e06b5f9323",
        "policy": "47ab55738a36730d0aaf603b0ae0896e7f0c17f91b0422be5e40efe0238b4f53",
    },
    # Explicit zero targets preserve the exact deployed V254 services/RLS.
    # Only V255 is admitted; the independent V251/V252 mainline stays excluded.
    "V255": {
        "count": 214,
        "ledger": "ad1e7af843327381b3740fec3fa9a91aab55d2ebbd31168e537372b0e395e971",
        "registered_count": 245,
        "registered": "4de182750a0b54598a9e873b5663aabdadfbed1be3de24b7c97310b2babf050a",
        "sql": (
            "migrations/V255__explicit_zero_targets.sql",
            "57953a249e9d8a6524b69b6b6f8d1f6431db5cd080f13c86764f67e983c65828",
        ),
        "services": "2fe0025cf798d9886a9959ac9e222c8058c08827e7dbc9a7436588e06b5f9323",
        "policy": "d417f8806738bb23e75415216b383d5eddb503b3ff5d8ac1abf2ad41b1713a74",
    },
}
GATE_FILES = (
    "review_release_contract.py",
    "identity_release_contract.py",
    "release121_contract.py",
    "release_upgrade.py",
    "release_forward_bundle.py",
    "release120_production_acl.py",
)


def frozen_stage(schema, *, require):
    require(schema in STAGES and isinstance(STAGES[schema], dict), "Review release stage is not frozen")
    stage = STAGES[schema]
    number = int(schema[1:])
    if schema in {"V253", "V254", "V255"}:
        expected_count, expected_registered = {
            "V253": (212, 243), "V254": (213, 244), "V255": (214, 245),
        }[schema]
        require(
            set(stage) == {"count", "ledger", "registered_count", "registered", "sql", "services", "policy"}
            and stage["count"] == expected_count and stage["registered_count"] == expected_registered,
            "Review history release stage metadata differs",
        )
        return stage
    require(
        all(isinstance(STAGES[f"V{n}"], dict) for n in range(240, number + 1)),
        "Review release prefix is not completely frozen",
    )
    require(
        set(stage) == {"count", "ledger", "registered_count", "registered", "sql", "services", "policy"}
        and stage["count"] == 203 + number - 239
        and stage["registered_count"] == 234 + number - 239,
        "Review release stage metadata differs",
    )
    return stage


def ledger_contract(schema, *, require):
    if schema == "V239":
        return dict(count=203, ledger=BASE_LEDGER, registered_count=234, registered=BASE_REGISTERED)
    if schema == "V250":
        return HISTORY_SOURCE
    return frozen_stage(schema, require=require)


def validate_migrations(migrations, prior, registered, source, target, pending, *, digest, require):
    final = frozen_stage(target, require=require)
    if target in {"V253", "V254", "V255"}:
        # The final self-check is allowed; no other starting ledger may enter
        # these sparse increments. Existing forward() still executes each once.
        if target == "V253":
            allowed_sources, expected_pending, forbidden = ("V250", "V253"), ["V253"], {"V251", "V252"}
        elif target == "V254":
            allowed_sources, expected_pending, forbidden = ("V253", "V254"), ["V254"], {"V251", "V252"}
        else:
            allowed_sources, expected_pending, forbidden = ("V254", "V255"), ["V255"], {"V251", "V252"}
        require(source in allowed_sources, "Review history requires the exact frozen source ledger")
        before = ledger_contract(source, require=require)
        after = {row["key"]: row["sha256"] for row in migrations}
        registered = list(registered)
        if source == target:
            expected_pending = []
        require(
            len(migrations) == len(after) == final["count"]
            and digest(after) == final["ledger"]
            and next(((row.get("file"), row["sha256"]) for row in migrations if row["key"] == target), None)
                == final["sql"]
            and not (forbidden & (set(after) | set(prior) | set(registered))),
            "Review history requires the exact complete ledger and only the frozen increment SQL",
        )
        require(
            len(prior) == before["count"] and digest(prior) == before["ledger"]
            and len(registered) == len(set(registered)) == before["registered_count"]
            and digest(sorted(registered)) == before["registered"],
            "Review history source ledger or registrations differ",
        )
        require(
            pending == expected_pending
            and set(after) == set(prior) | set(expected_pending)
            and all(after[key] == value for key, value in prior.items()),
            "Review history requires only its exact unchanged-prefix increment",
        )
        return
    before = ledger_contract(source, require=require)
    require(int(source[1:]) <= int(target[1:]), "Review schema downgrade is forbidden")
    after = {row["key"]: row["sha256"] for row in migrations}
    sql = {f"V{n}": frozen_stage(f"V{n}", require=require)["sql"] for n in range(240, int(target[1:]) + 1)}
    require(
        len(migrations) == len(after) == final["count"]
        and digest(after) == final["ledger"]
        and {row["key"]: (row.get("file"), row["sha256"]) for row in migrations if row["key"] in sql} == sql,
        "Review release requires the exact complete ledger and SQL paths",
    )
    registered = list(registered)
    require(
        len(registered) == len(set(registered)) == before["registered_count"]
        and digest(sorted(registered)) == before["registered"]
        and len(prior) == before["count"]
        and digest(prior) == before["ledger"],
        "Review release source ledger or registrations differ",
    )
    expected_pending = [f"V{n}" for n in range(int(source[1:]) + 1, int(target[1:]) + 1)]
    require(
        pending == expected_pending
        and set(after) == set(prior) | set(expected_pending)
        and all(after[key] == value for key, value in prior.items()),
        "Review release requires only its exact unchanged-prefix increment",
    )


def validate(plan, migrations, *, digest, require):
    require(
        plan.get("product_version") == "1.2.1"
        and plan.get("release_scope") == RELEASE_SCOPE
        and bool(plan["pending"])
        and plan.get("agent_stage") is None
        and plan.get("prepared_configuration_delta") is None,
        "Review release requires fixed scope and unchanged Agent configuration",
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
        and bool(versions)
        and all(isinstance(v, str) and v.startswith("V") and v[1:].isdigit() for v in versions)
        and versions == sorted(set(versions)),
        "Review ACL registrations must be exact",
    )
    schema = max(versions, key=lambda value: int(value[1:]))
    stage = frozen_stage(schema, require=require)
    require(
        len(versions) == stage["registered_count"] and digest(versions) == stage["registered"],
        "Review ACL requires the exact registered ledger",
    )
    return schema, stage


def validate_acl(plan, policy, *, digest, require):
    schema, stage = schema_contract(plan, digest=digest, require=require)
    require(
        policy.get("schema") == schema
        and policy.get("migration_fingerprint") == stage["ledger"]
        and digest(policy.get("services")) == stage["services"]
        and digest(policy) == stage["policy"],
        "Review ACL requires the exact frozen service and RLS policy",
    )
