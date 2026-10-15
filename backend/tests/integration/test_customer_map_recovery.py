"""Annual map coverage under real RLS; synthetic assessments, no model calls."""

from types import SimpleNamespace

import pytest

from sales_backend.api.customer_assets import map_customers
from sales_backend.config import get_settings
from sales_backend.db import Database
from sales_backend.repositories.customer_assets import today
from sales_backend.services.customer_map import read_map, refresh_missing_map
from tests.integration.connection_pool import FixtureConnectionPool
from tests.integration.test_customer_assets import customer
from tests.integration.test_customer_map_projection import formal_visit
from tests.integration.test_fde_battle_map_reviews import answer, handler_for
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def fixture(connection):
    sales = await actor(connection, "XS001")
    client = await customer(connection, sales)
    await formal_visit(connection, sales, client["id"], today().isoformat())
    return sales, client


async def score(connection, who, customer_id, potential=75):
    handler = handler_for(connection)
    facts = await handler._load_facts(customer_id, who)
    await handler._persist(customer_id, who, facts, answer(handler, facts, potential), {})


async def jobs(connection, customer_id):
    return await connection.fetch(
        "SELECT id,status,payload FROM ops.job WHERE aggregate_id=$1::uuid AND job_type='battle_map.review'",
        customer_id,
    )


async def test_default_map_keeps_visible_score_but_person_scope_never_borrows_it(connection):
    sales, client = await fixture(connection)
    manager = await actor(connection, "ZJL001")
    await score(connection, manager, client["id"], 81)
    rows = await read_map(connection, manager, as_of=today())
    point = next(row for row in rows if row["id"] == client["id"])
    assert point["potential_score"] == 81
    assert point["assessment_id"] and point["subject_user_ref_id"] == manager.user_id
    own = await read_map(connection, manager, as_of=today(), scope="person", member_id=sales.user_id)
    point = next(row for row in own if row["id"] == client["id"])
    assert point["potential_score"] is None and point["assessment_id"] is None
    assert not await jobs(connection, client["id"])


@pytest.mark.parametrize("mode", [
    "empty", "owner", "missing_owner_score", "other_person", "team", "department", "expired_department",
])
async def test_combined_subject_projection_matches_previous_rls_overlay(connection, mode):
    """Compare against the independent former customer + subject query path."""
    from sales_backend.db import set_request_context
    from sales_backend.repositories.customer_map import CustomerMapRepository
    from sales_backend.repositories.profile_customers import subject_customers

    assessment_fields = ("potential_score", "relationship_score", "quadrant_code", "quadrant_policy",
                         "assessment_id", "subject_user_ref_id")

    sales, client = await fixture(connection)
    if mode != "missing_owner_score":
        await score(connection, sales, client["id"], 23)
    other = await actor(connection, "XS002")
    manager = await actor(connection, "ZJL001")
    # A newer, different score must not win over a matching subject's score.
    await score(connection, manager, client["id"], 81)
    team_case = mode in ("team", "department", "expired_department")
    if team_case:
        # Make the owner score newer. An active team member's older score must
        # still win, but an expired department membership must not select it.
        supervisor = await actor(connection, "ZJ001")
        await score(connection, supervisor, client["id"], 66)
        await set_request_context(connection, sales)
        await score(connection, sales, client["id"], 23)
    if mode == "expired_department":
        await actor(connection, "ADMIN001")
        await connection.execute(
            "UPDATE platform.team_membership SET valid_from=clock_timestamp()-interval '2 days',"
            "valid_to=clock_timestamp()-interval '1 day' WHERE user_ref_id=$1::uuid", supervisor.user_id,
        )
    await set_request_context(connection, manager)
    selected_person = supervisor.user_id if team_case else sales.user_id if mode in (
        "owner", "missing_owner_score") else other.user_id
    people = [] if mode == "empty" else [selected_person]
    scope = "team" if mode == "team" else "department" if "department" in mode else "person"
    explicit = mode in ("owner", "missing_owner_score", "other_person", "empty")
    params = {"member_ids": people, "scope": scope, "team_ids": list(sales.team_ids)}
    base = await CustomerMapRepository().read(connection, as_of=today())
    selected = {row["id"]: row for row in await subject_customers(
        connection, **params, customer_ids=[row["id"] for row in base],
    )} if people else {}
    expected = [
        {**row, **{key: selected[row["id"]].get(key) for key in assessment_fields}}
        if row["id"] in selected and (explicit or selected[row["id"]].get("assessment_id")) else row
        for row in base
    ]
    actual = await CustomerMapRepository().read(
        connection, as_of=today(), subject_member_ids=people, subject_scope=scope,
        subject_team_ids=params["team_ids"], explicit_subject=explicit,
    )
    assert actual == expected
    point = next(row for row in actual if row["id"] == client["id"])
    assert point["potential_score"] == (
        23 if mode in ("owner", "expired_department") else None if mode == "missing_owner_score"
        else 66 if team_case else 81)
    # Repository selectors cannot confer visibility to an unrelated salesperson.
    await set_request_context(connection, other)
    denied = await CustomerMapRepository().read(
        connection, as_of=today(), subject_member_ids=[sales.user_id], subject_scope="team",
        subject_team_ids=params["team_ids"], explicit_subject=True,
    )
    assert client["id"] not in {row["id"] for row in denied}


async def test_read_only_api_reports_complete_active_set_and_never_starts_jobs(connection):
    sales, client = await fixture(connection)
    database = Database(get_settings(), pool=FixtureConnectionPool(connection))
    result = await map_customers(scope=None, member_id=None, member_ids=[], team_id=None,
                                 identity=SimpleNamespace(actor=sales), database=database)
    assert result["map_status"] == "updating"
    # Released 1.1.1 omits the window and must retain its annual contract.
    assert result["activity_since"] == today().replace(month=1, day=1)
    recent = await map_customers(activity_window="today", scope=None, member_id=None,
                                 member_ids=[], team_id=None,
                                 identity=SimpleNamespace(actor=sales), database=database)
    assert recent["activity_since"] == today()
    assert result["active_customer_count"] == len(result["items"])
    assert client["id"] in {row["id"] for row in result["items"]}
    assert not await jobs(connection, client["id"])


async def test_refresh_is_idempotent_and_valid_score_converges_without_overwrite(connection):
    sales, client = await fixture(connection)
    first = await refresh_missing_map(connection, sales, as_of=today())
    assert first == {"status": "updating", "enqueued_count": 1}
    second = await refresh_missing_map(connection, sales, as_of=today())
    assert second == {"status": "updating", "enqueued_count": 0}
    queued = await jobs(connection, client["id"])
    assert len(queued) == 1
    assert queued[0]["payload"]["user_id"] == sales.user_id
    assert queued[0]["payload"]["trigger_type"] == "map.coverage_recovery"
    await score(connection, sales, client["id"], 0)
    assert await refresh_missing_map(connection, sales, as_of=today()) == {"status": "ready", "enqueued_count": 0}
    point = next(row for row in await read_map(connection, sales, as_of=today()) if row["id"] == client["id"])
    assert point["potential_score"] == 0 and point["relationship_score"] == 50
    assert len(await jobs(connection, client["id"])) == 1


async def test_refresh_reuses_regular_visit_job_and_never_retries_terminal_jobs_in_a_loop(connection):
    from uuid import uuid4

    from sales_backend.repositories.jobs import enqueue_battle_map_review

    sales, client = await fixture(connection)
    await enqueue_battle_map_review(connection, sales, customer_id=client["id"],
                                   trigger_type="visit.archived", trigger_id=str(uuid4()))
    assert (await refresh_missing_map(connection, sales, as_of=today()))["enqueued_count"] == 0
    job = (await jobs(connection, client["id"]))[0]
    await connection.execute(
        "UPDATE ops.job SET status='dead_letter',completed_at=clock_timestamp() WHERE id=$1", job["id"])
    failed = await refresh_missing_map(connection, sales, as_of=today())
    assert failed["status"] == "unavailable" and failed["enqueued_count"] == 0
    await connection.execute(
        "UPDATE ops.job SET completed_at=clock_timestamp()-interval '11 minutes' WHERE id=$1", job["id"])
    retried = await refresh_missing_map(connection, sales, as_of=today())
    assert retried == {"status": "updating", "enqueued_count": 1}
    old = next(row for row in await jobs(connection, client["id"]) if row["id"] == job["id"])
    assert old["status"] == "dead_letter"  # Historical evidence remains intact.


async def test_refresh_does_not_proxy_other_people_or_include_inaccessible_customers(connection):
    sales, client = await fixture(connection)
    other = await actor(connection, "XS002")
    assert not await connection.fetchval("SELECT security.has_customer_access($1::uuid)", client["id"])
    result = await refresh_missing_map(connection, other, as_of=today())
    assert result == {"status": "ready", "enqueued_count": 0}
    denied = await refresh_missing_map(connection, other, as_of=today(), member_id=sales.user_id, scope="person")
    assert denied["status"] == "unavailable" and denied["enqueued_count"] == 0
    await actor(connection, "XS001")
    assert not await jobs(connection, client["id"])


@pytest.mark.parametrize("viewer,selected", [
    ("lead", ()),
    ("lead", ("first",)),
    ("lead", ("first", "second")),
    ("first", ()),
])
async def test_fde_map_recovery_uses_current_actor_for_the_selected_customer_scope(connection, viewer, selected):
    from sales_backend.db import set_request_context
    from tests.integration.test_fde_identity_tasks import fde_fixture

    sales, opportunity, people, _ = await fde_fixture(connection)
    await set_request_context(connection, sales)
    await formal_visit(connection, sales, opportunity["customer_id"], today().isoformat(), opportunity["id"])
    # This customer has a valid visit but no collaboration link for this FDE.
    unrelated = await customer(connection, sales)
    await formal_visit(connection, sales, unrelated["id"], today().isoformat())
    who = await actor(connection, people[viewer]["code"])
    params = {"scope": "team" if viewer == "lead" else "self",
              "member_ids": [people[label]["id"] for label in selected]}
    before = await read_map(connection, who, as_of=today(), **params)
    assert [row["id"] for row in before] == [opportunity["customer_id"]]
    assert before[0]["assessment_id"] is None
    assert await refresh_missing_map(connection, who, as_of=today(), **params) == {
        "status": "updating", "enqueued_count": 1,
    }
    assert await refresh_missing_map(connection, who, as_of=today(), **params) == {
        "status": "updating", "enqueued_count": 0,
    }
    queued = [job for job in await jobs(connection, opportunity["customer_id"])
              if job["payload"]["user_id"] == who.user_id]
    assert len(queued) == 1
    assert queued[0]["payload"]["user_id"] == who.user_id
    assert queued[0]["payload"]["role"] == who.role.value
    handler = handler_for(connection)
    facts = await handler._load_facts(opportunity["customer_id"], who)
    await handler._persist(opportunity["customer_id"], who, facts, answer(handler, facts, 73), queued[0]["payload"])
    assert await refresh_missing_map(connection, who, as_of=today(), **params) == {
        "status": "ready", "enqueued_count": 0,
    }
    after = await read_map(connection, who, as_of=today(), **params)
    assert len(after) == 1
    assert after[0]["subject_user_ref_id"] == who.user_id
    assert after[0]["assessment_id"] and after[0]["potential_score"] == 73

    await actor(connection, "ADMIN001")
    score_owners = await connection.fetch(
        "SELECT subject_user_ref_id::text FROM insight.quadrant_score "
        "WHERE customer_id=$1::uuid AND valid_to='infinity'", opportunity["customer_id"],
    )
    assert {row["subject_user_ref_id"] for row in score_owners} == {who.user_id}
    assert not await jobs(connection, unrelated["id"])
    # Recovery does not pretend that a new visit occurred or notify the sales hierarchy.
    for code in ("ZJ001", "ZJL001"):
        await actor(connection, code)
        assert await connection.fetchval(
            "SELECT count(*) FROM workflow.notification WHERE template_code='battle_map_updated' "
            "AND payload->>'trigger_id'=$1", queued[0]["payload"]["trigger_id"],
        ) == 0
