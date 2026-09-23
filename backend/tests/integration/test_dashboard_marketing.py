import pytest

from sales_backend.domain.agent import RoleCode
from sales_backend.repositories.dashboard import DashboardRepository
from sales_backend.repositories.profile import ProfileRepository
from sales_backend.repositories.workbench import WorkbenchRepository

pytestmark = pytest.mark.asyncio


async def test_dashboard_full_scope_matches_database_and_workbench(connection, actor_factory):
    for role in (RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER):
        actor = await actor_factory(role)
        data = await DashboardRepository().load(connection, actor)
        expected = await connection.fetch(
            """SELECT o.id::text FROM crm.opportunity o
            JOIN crm.customer c ON c.id=o.customer_id AND c.deleted_at IS NULL
            WHERE o.deleted_at IS NULL AND o.status='open' AND
              ($1='manager' OR ($1='sales' AND o.owner_user_ref_id=$2::uuid)
              OR ($1='supervisor' AND o.owner_team_id=ANY($3::uuid[])))""",
            role.value,
            actor.user_id,
            list(actor.team_ids),
        )
        assert {row["id"] for row in data["opportunities"]} == {row["id"] for row in expected}
        workbench = await WorkbenchRepository().load(connection, actor)
        assert workbench["summary"]["opportunities"] == len(expected)
        assert workbench["summary"]["forecast"] == sum(row["amount"] or 0 for row in data["opportunities"])
        assert workbench["summary"]["open_amount"] == workbench["summary"]["forecast"]
        personal = await DashboardRepository().load(connection, actor, personal=True)
        assert all(row["owner_id"] == actor.user_id for row in personal["opportunities"])
        assert all(row["owner_id"] == actor.user_id for row in personal["quarter_forecasts"])
        assert all(row["recorder_id"] == actor.user_id for row in personal["recent_visits"])


async def test_efficiency_count_and_periods_match_persisted_visits(connection, actor_factory):
    for role in (RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER):
        actor = await actor_factory(role)
        data = await ProfileRepository().evaluation_summary(connection, actor)
        efficiency = data["efficiency"]
        if role == RoleCode.SALES:
            assert actor.user_id in {row["user_id"] for row in efficiency["followup"]["year"]}
            assert all(row["role"] == "sales" for row in efficiency["followup"]["year"])
        for row in efficiency["followup"]["year"]:
            count = await connection.fetchval(
                """SELECT count(*) FROM activity.visit WHERE recorder_user_ref_id=$1::uuid
                AND deleted_at IS NULL AND status IN ('confirmed','archived')
                AND timezone('Asia/Shanghai',interaction_at)::date BETWEEN
                  date_trunc('year',timezone('Asia/Shanghai',clock_timestamp()))::date
                  AND timezone('Asia/Shanghai',clock_timestamp())::date""",
                row["user_id"],
            )
            assert row["value"] == count
        for metric in ("customers", "opportunities"):
            yearly = {row["user_id"]: row["value"] for row in efficiency[metric]["year"]}
            assert all(row["value"] >= yearly[row["user_id"]] for row in efficiency[metric]["all"])


async def test_actuals_exclude_voided_and_preserve_unrecorded(connection, manager_actor):
    data = await DashboardRepository().load(connection, manager_actor)
    for quarter in data["quarter_actuals"]:
        for kind in ("recognized", "collection"):
            expected = await connection.fetchrow(
                """SELECT sum(a.amount) AS amount,count(*) AS count FROM crm.customer_actual a
                JOIN crm.customer c ON c.id=a.customer_id AND c.deleted_at IS NULL
                WHERE a.voided_at IS NULL AND a.kind=$1 AND extract(year FROM a.occurred_on)=$2
                  AND extract(quarter FROM a.occurred_on)=$3
                  AND a.occurred_on <= timezone('Asia/Shanghai',clock_timestamp())::date""",
                kind,
                quarter["year"],
                quarter["quarter"],
            )
            assert quarter[kind + "_amount"] == expected["amount"]
            assert quarter[kind + "_count"] == expected["count"]
