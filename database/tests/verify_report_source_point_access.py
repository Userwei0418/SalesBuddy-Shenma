"""Runtime-role equivalence and bounded local plan/timing evidence.

Only called inside the whole-bridge verifier's disposable databases. Baseline
definitions and synthetic rows are transactionally rolled back. No external IO.
"""

import json
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNATURE = "CREATE OR REPLACE FUNCTION security.work_report_source_access(p_manifest jsonb,p_selected jsonb) RETURNS boolean"


def definition(path):
    text = path.read_text()
    start = text.index(SIGNATURE)
    return text[start : text.index("$$;", start) + 3]


def source(kind, ident, **extra):
    return {"source_type": kind, "record_id": str(ident), **extra}


async def verify(conn, role, f, context):
    before = definition(
        ROOT / "migrations/V393__internal_work_report_review_freeze.sql"
    )
    after = definition(ROOT / "migrations/V399__shenma_whole_121_delivery_guards.sql")
    outer = conn.transaction()
    await outer.start()
    try:
        await conn.execute("SET LOCAL statement_timeout='15s'")
        # The comparison function is also SECURITY INVOKER, uses the exact old
        # body, and is callable only in this owned disposable database.
        await conn.execute(
            before.replace(
                "security.work_report_source_access(",
                "security.test_report_source_access_before(",
                1,
            )
        )
        await conn.execute(
            "REVOKE ALL ON FUNCTION security.test_report_source_access_before(jsonb,jsonb) FROM PUBLIC"
        )
        await conn.execute(
            f'GRANT EXECUTE ON FUNCTION security.test_report_source_access_before(jsonb,jsonb) TO "{role}"'
        )
        await context(conn, f["ws"], f["admin"], "administrator")
        ids = {}
        for label, ws, owner, deleted in (
            ("other_owner", f["ws"], f["admin"], False),
            ("deleted", f["ws"], f["sales"], True),
            ("other_workspace", f["demo"], f["demo_user"], False),
        ):
            customer, opportunity = uuid.uuid4(), uuid.uuid4()
            await conn.execute(
                """INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,
              owner_user_ref_id,deleted_at) VALUES($1,$2,$3,$3,$4,$4,CASE WHEN $5 THEN clock_timestamp() END)""",
                customer,
                ws,
                "point-guard-" + label,
                owner,
                deleted,
            )
            await conn.execute(
                """INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,deleted_at)
              VALUES($1,$2,$3,$4,$5,CASE WHEN $6 THEN clock_timestamp() END)""",
                opportunity,
                ws,
                customer,
                "point-guard-" + label,
                owner,
                deleted,
            )
            ids[label] = (customer, opportunity)
        cases = [
            (
                "own opportunity",
                [source("opportunity", f["opportunity"])],
                ["opportunity"],
                True,
            ),
            ("own customer", [source("meeting_customer", f["customer"])], [], True),
            ("archived visit", [source("visit", f["visit"])], ["visit"], True),
            ("empty", [], [], True),
            ("unknown source", [source("unknown", uuid.uuid4())], [], False),
            ("unknown selected", [], ["unknown"], False),
            (
                "review self",
                [
                    source(
                        "review_subject",
                        f["sales"],
                        scope_type="person",
                        required_permissions=["work_report.read"],
                    )
                ],
                [],
                True,
            ),
        ]
        for kind, offset in (("meeting_customer", 0), ("opportunity", 1)):
            for label in ids:
                cases.append(
                    (kind + " " + label, [source(kind, ids[label][offset])], [], False)
                )
            cases.append((kind + " missing", [source(kind, uuid.uuid4())], [], False))
        for kind in (
            "review_subject",
            "review_target",
            "review_objective",
            "review_key_result",
            "review_actual",
        ):
            cases.append(
                (kind + " missing provenance", [source(kind, uuid.uuid4())], [], False)
            )

        await conn.execute(f'SET LOCAL ROLE "{role}"')
        await conn.execute("SET LOCAL row_security=on")
        assert await conn.fetchval(
            "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        await context(conn, f["ws"], f["sales"], "sales", f["team"])
        await conn.execute("SELECT security.authorization_refresh()")

        async def compare(label, manifest, selected, expected):
            result = await conn.fetchrow(
                """SELECT security.test_report_source_access_before($1::jsonb,$2::jsonb) AS before,
              security.work_report_source_access($1::jsonb,$2::jsonb) AS after""",
                json.dumps(manifest),
                json.dumps(selected),
            )
            assert result["before"] is expected and result["after"] is expected, (
                label,
                dict(result),
                expected,
            )

        for case in cases:
            await compare(*case)
        await conn.execute("RESET ROLE")
        # Revocation changes must be observed after cache refresh by both forms.
        for permission in ("opportunity.read", "customer.read", "work_report.read"):
            nested = conn.transaction()
            await nested.start()
            try:
                await conn.execute(
                    """INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect)
                  VALUES($1,$2,$3,'deny') ON CONFLICT(workspace_id,user_ref_id,permission_code) DO UPDATE SET effect='deny'""",
                    f["ws"],
                    f["sales"],
                    permission,
                )
                await conn.execute(f'SET LOCAL ROLE "{role}"')
                await context(conn, f["ws"], f["sales"], "sales", f["team"])
                await conn.execute("SELECT security.authorization_refresh()")
                kind, ident = (
                    ("meeting_customer", f["customer"])
                    if permission == "customer.read"
                    else ("opportunity", f["opportunity"])
                )
                await compare("revoked " + permission, [source(kind, ident)], [], False)
            finally:
                await nested.rollback()
        await conn.execute("RESET ROLE")
        # Add unrelated visible rows to make an uncorrelated scan detectable;
        # four manifest entries model the observed clone request, without data.
        await context(conn, f["ws"], f["admin"], "administrator")
        extra = [uuid.uuid4() for _ in range(96)]
        await conn.executemany(
            """INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id)
          VALUES($1,$2,$3,$4,$5,$6)""",
            [
                (
                    ident,
                    f["ws"],
                    f["customer"],
                    "point-plan-" + str(i),
                    f["sales"],
                    f["team"],
                )
                for i, ident in enumerate(extra)
            ],
        )
        snapshot, run = uuid.uuid4(), uuid.uuid4()
        payload = {"sources": [source("opportunity", ident) for ident in extra[:4]]}
        await conn.execute(
            """INSERT INTO agent.work_report_snapshot(id,workspace_id,owner_user_ref_id,selection,period,scope,
          status,snapshot_hash,payload,data_as_of) VALUES($1,$2,$3,'{"sources":["visit","opportunity"]}','{}','{}',
          'ready',$4,$5::jsonb,clock_timestamp())""",
            snapshot,
            f["ws"],
            f["sales"],
            "a" * 64,
            json.dumps(payload),
        )
        await conn.execute(
            """INSERT INTO agent.work_report_run(id,workspace_id,owner_user_ref_id,snapshot_id,request,generation_input)
          VALUES($1,$2,$3,$4,'{}','{}')""",
            run,
            f["ws"],
            f["sales"],
            snapshot,
        )
        await conn.execute(
            "ANALYZE crm.opportunity; ANALYZE agent.work_report_snapshot; ANALYZE agent.work_report_run"
        )
        timings = {}
        query = "SELECT * FROM agent.work_report_run WHERE id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid FOR UPDATE"
        for name, sql in (("before", before), ("after", after)):
            await conn.execute(sql)
            await conn.execute(f'SET LOCAL ROLE "{role}"')
            await context(conn, f["ws"], f["sales"], "sales", f["team"])
            await conn.execute("SELECT security.authorization_refresh()")
            timings[name] = {}
            for mode in ("force_custom_plan", "force_generic_plan"):
                await conn.execute("SELECT set_config('plan_cache_mode',$1,true)", mode)
                start = time.monotonic()
                row = await conn.fetchrow(query, run, f["ws"], f["sales"])
                timings[name][mode + "_ms"] = round(
                    (time.monotonic() - start) * 1000, 3
                )
                assert row["id"] == run
            await conn.execute("RESET ROLE")
        # No absent-source or other-author report can be locked by this actor.
        await conn.execute(f'SET LOCAL ROLE "{role}"')
        await context(conn, f["ws"], f["sales"], "sales", f["team"])
        assert await conn.fetchrow(query, uuid.uuid4(), f["ws"], f["sales"]) is None
        await context(conn, f["ws"], f["admin"], "administrator")
        await conn.execute("SELECT security.authorization_refresh()")
        assert await conn.fetchrow(query, run, f["ws"], f["sales"]) is None
        return {
            "equivalence_cases": len(cases) + 3,
            "restricted_role": "NOSUPERUSER NOBYPASSRLS",
            "opportunity_rows_added": 96,
            "manifest_opportunities": 4,
            "same_run_for_update_ms": timings,
            "qualification": "Local synthetic timings, not clone or production latency proof",
        }
    finally:
        await outer.rollback()
