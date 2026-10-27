"""Focused real PG/ASGI proof; run the V257 verifier with --focus contribution."""

# ruff: noqa: E501, S608 -- Fixed internal fixture table names.
from uuid import uuid4

import asyncpg


async def verify_contribution(c, db, actors, ws, team, task, legacy, read, command, report):
    async def claim():
        return await c.fetchval("SELECT actual_contribution FROM workflow.task_alignment WHERE task_id=$1", task)

    async def history():
        return await c.fetchval(
            "SELECT count(*) FROM workflow.task_alignment_history WHERE task_id=$1 AND event_kind='reported_contribution'",
            task,
        )

    async def business_facts():
        return {
            table: await c.fetchval(
                f"SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text),'[]') FROM {table} r"
            )
            for table in (
                "crm.opportunity",
                "crm.sales_target",
                "crm.review_key_result_progress",
                "crm.opportunity_actual_confirmation",
                "crm.opportunity_actual_basis",
                "crm.customer_actual",
            )
        }

    # Existing public alignment function controls real metric association.
    async with db.transaction(actors["reviewer"]) as tx:
        state = await tx.fetchval("SELECT workflow.task_execution_read($1::uuid)", task)
        await tx.fetchval(
            "SELECT security.save_task_alignment($1::uuid,$2::jsonb,$3)",
            task,
            {"metric_codes": ["collection"], "key_result_ids": []},
            state["version_no"],
        )
    await command("accept_task", {})
    state = await read()
    for item in state["items"]:
        await command(
            "set_item_progress",
            {
                "item_id": item["item_id"],
                "definition_version": item["definition_version"],
                "state": "completed",
                "note": "delivered",
            },
        )
    state = await read()
    assert state["progress"]["percent"] == 100 and state["status"] != "completed" and await claim() is None
    facts_before = await business_facts()
    report["checks"].append("100% checklist has no inferred contribution or automatic completion")

    declaration = {"metric_code": "collection", "value": "9999.99", "note": "declared evidence"}
    before = await read()
    for invalid in ({**declaration, "metric_code": "daily_routine"}, {**declaration, "value": "1.001"}):
        await command("submit_completion", {"note": "bad claim", "actual_contribution": invalid}, expected=422)
    assert await read() == before and await claim() is None and await history() == 0
    report["checks"].append(
        "unlinked metric and malformed precision reject atomically without task/event/claim/history mutation"
    )

    # Failure after record_task_contribution must roll back the whole HTTP transaction.
    await c.execute(
        "CREATE FUNCTION public.task_test_fail_submission() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'TASK_TEST_ROLLBACK' USING ERRCODE='22023'; END $$"
    )
    await c.execute(
        "CREATE TRIGGER task_test_fail_submission BEFORE INSERT ON workflow.task_execution_submission FOR EACH ROW EXECUTE FUNCTION public.task_test_fail_submission()"
    )
    await command("submit_completion", {"note": "rollback proof", "actual_contribution": declaration}, expected=422)
    await c.execute("DROP TRIGGER task_test_fail_submission ON workflow.task_execution_submission")
    await c.execute("DROP FUNCTION public.task_test_fail_submission()")
    assert await read() == before and await claim() is None and await history() == 0
    report["checks"].append(
        "injected post-declaration submission failure rolls back claim, history, event, receipt and task version together"
    )

    key = str(uuid4())
    response, body = await command(
        "submit_completion", {"note": "first delivery", "actual_contribution": declaration}, key=key
    )
    execution = response["execution"]
    assert execution["status"] == "pending_review" and execution["version_no"] == before["version_no"] + 1, {
        "before": before["version_no"],
        "after": execution["version_no"],
        "status": execution["status"],
    }
    first = execution["submissions"][-1]
    actual = await claim()
    assert actual["value"] == declaration["value"] and actual["status"] == "reported"
    assert actual["task_version"] == execution["version_no"]
    assert actual["reported_by_user_id"] == actors["executor"].user_id and actual["reported_at"]
    assert actual["counts_toward_financial_actuals"] is False
    assert first["snapshot"]["actual_contribution"] == actual and await history() == 1
    replay, _ = await command("submit_completion", {}, body=body, key=key)
    assert replay == response and await history() == 1
    await command(
        "submit_completion",
        {},
        body={**body, "actual_contribution": {**declaration, "value": "2"}},
        key=key,
        expected=409,
    )
    report["checks"].append(
        "HTTP declaration persists at submission time with one version increment, complete immutable reported metadata, identical replay and conflicting-body rejection"
    )

    item = execution["items"][0]
    await command(
        "review_completion",
        {
            "submission_id": execution["current_submission"],
            "decision": "reject",
            "note": "redo",
            "rework_item_ids": [item["item_id"]],
        },
        actor="reviewer",
    )
    assert await claim() == actual and (await read())["submissions"][0] == first
    await command(
        "set_item_progress",
        {
            "item_id": item["item_id"],
            "definition_version": item["definition_version"],
            "state": "completed",
            "note": "revised delivery",
        },
    )
    response, _ = await command(
        "submit_completion", {"note": "second delivery", "actual_contribution": {**declaration, "value": "10000"}}
    )
    second = response["execution"]["submissions"][-1]
    second_claim = await claim()
    assert (
        second_claim["value"] == "10000"
        and second["snapshot"]["actual_contribution"] == second_claim
        and await history() == 2
    )
    assert (await read())["submissions"][0] == first
    await command(
        "withdraw_completion", {"submission_id": response["execution"]["current_submission"], "reason": "supplement"}
    )
    assert await claim() == second_claim and await history() == 2
    report["checks"].append(
        "review rejection and withdrawal retain declarations; explicit resubmission appends history while earlier completion snapshots remain unchanged"
    )

    # Old receipt without the new optional field replays identically with JSON null.
    key = str(uuid4())
    response, body = await command("submit_completion", {"note": "no new declaration"}, key=key)
    assert response["execution"]["submissions"][-1]["snapshot"]["actual_contribution"] == second_claim
    stored_body = await c.fetchval("SELECT body FROM workflow.task_execution_request WHERE key=$1::uuid", key)
    assert "actual_contribution" not in stored_body and await history() == 2
    replay, _ = await command("submit_completion", {}, body={**body, "actual_contribution": None}, key=key)
    assert replay == response and await history() == 2
    # Direct SQL also normalizes null before comparing a pre-extension receipt.
    async with db.transaction(actors["executor"]) as tx:
        direct = await tx.fetchval(
            "SELECT workflow.task_execution_command($1::uuid,'submit_completion',$2::jsonb,$3::uuid)",
            task,
            {**body, "actual_contribution": None},
            key,
        )
        assert direct == response
    assert await business_facts() == facts_before
    report["checks"].append(
        "omission/null HTTP and SQL replay preserve old receipt body and current claim; financial and KR row contents unchanged"
    )

    await c.execute(
        "INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'operations','collaborator')",
        task,
        ws,
        actors["new_reviewer"].user_id,
        team,
    )
    guard_before = await read()
    attempts = (
        (
            "UPDATE workflow.task_assignee SET responsibility='collaborator' WHERE task_id=$1 AND responsibility='owner'",
            [task],
        ),
        (
            "UPDATE workflow.task_assignee SET responsibility='owner' WHERE task_id=$1 AND assignee_user_ref_id=$2",
            [task, actors["new_reviewer"].user_id],
        ),
        ("UPDATE workflow.task_assignee SET task_id=$2 WHERE task_id=$1 AND responsibility='owner'", [task, legacy]),
        (
            "UPDATE workflow.task_assignee SET task_id=$2,responsibility='collaborator' WHERE task_id=$1 AND responsibility='owner'",
            [task, legacy],
        ),
        (
            "UPDATE workflow.task_assignee SET assignee_user_ref_id=$2 WHERE task_id=$1 AND responsibility='owner'",
            [task, actors["reviewer"].user_id],
        ),
        ("DELETE FROM workflow.task_assignee WHERE task_id=$1 AND responsibility='owner'", [task]),
        (
            "INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'operations','owner')",
            [task, ws, actors["reviewer"].user_id, team],
        ),
    )
    for sql, arguments in attempts:
        try:
            async with db.transaction(actors["reviewer"]) as tx:
                assert await tx.fetchval("SELECT security.authorization_task('task.coordinate',$1)", task)
                await tx.execute(sql, *arguments)
        except asyncpg.SerializationError as exc:
            assert str(exc) == "TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED"
        else:
            raise AssertionError("owner bypass: " + sql)
    assert (
        await c.fetchval(
            "SELECT count(*) FROM workflow.task_assignee WHERE task_id=$1 AND responsibility='owner'", task
        )
        == 1
    )
    assert await read() == guard_before
    report["checks"].append(
        "c61 guard regression: 7 ordinary coordinator owner INSERT/UPDATE/DELETE/demotion/promotion/move mutations reject with 40001; owner, versions, projection and contribution snapshot unchanged"
    )

    await c.execute(
        "INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)",
        ws,
        actors["executor"].user_id,
    )
    await c.execute(
        "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'task.complete','deny',NULL)",
        ws,
        actors["executor"].user_id,
    )
    await command("submit_completion", {}, body=body, key=key, expected=403)
    assert await history() == 2
    report["checks"].append("current task.complete revocation blocks contribution completion receipt replay")
    report["samples"]["completion_snapshot"] = first
