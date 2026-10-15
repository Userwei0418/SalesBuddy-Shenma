"""Read deployed profile scopes without creating sessions, reviews, or business records.

Run with the deployed backend interpreter and its existing server environment.
Every service and map read shares one repeatable-read, read-only transaction, so
member/self comparisons cannot drift because another request changes a record.
Only role names, check counts and booleans are printed; exceptions are redacted.
"""

import asyncio
import json
from collections import Counter
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from sales_backend.api.customer_assets import map_customers
from sales_backend.config import get_settings
from sales_backend.db import Database, set_request_context
from sales_backend.repositories.collaboration import fde_scope_options
from sales_backend.repositories.customer_assets import today
from sales_backend.repositories.fde_dashboard import fde_dashboard
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.profile_scope import scope_options
from sales_backend.services.fde_profile import get_profile
from sales_backend.services.profile_performance import performance, quarter_period
from sales_backend.services.targets import read_targets

ROLES = ("sales", "supervisor", "manager", "fde", "fde_lead")
SALES_ROLES = ROLES[:3]
KINDS = ("collection", "recognized")


class Checks:
    def __init__(self):
        self.counts = Counter()

    def require(self, name, condition):
        if not condition:
            # Never include the failed value, actor, business row or exception text.
            raise AssertionError(name)
        self.counts[name] += 1


class ExistingReadonlyTransaction:
    """Let the actual map read handler use the already-authorized transaction."""

    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def transaction(self, actor, *, readonly=False):
        if not readonly:
            raise PermissionError("Read-only verification")
        await set_request_context(self.connection, actor)
        yield self.connection


def check_ratio(checks, value):
    numerator, denominator = value["numerator"], value["denominator"]
    evaluated = value["evaluated_count"]
    checks.require("ratio_counts", 0 <= numerator <= evaluated <= denominator)
    checks.require("pending_coverage", value["pending_count"] == denominator - evaluated)
    expected_coverage = Decimal(evaluated) * 100 / denominator if denominator else None
    expected_rate = Decimal(numerator) * 100 / denominator if denominator and evaluated else None
    checks.require("ratio_formula", value["rate"] == expected_rate and value["coverage"] == expected_coverage)


async def check_targets(connection, actor, checks, current, *, scope="self", user_id=None, team_id=None):
    result = await read_targets(
        connection, actor, period_type="quarter", anchor_date=current["start"],
        scope=scope, user_id=user_id, team_id=team_id,
    )
    checks.require("quarter_target_period", result["period"] == {
        key: current[key] for key in ("type", "start", "end")
    })
    checks.require("target_rows_same_quarter", all(
        row["period_type"] == "quarter" and row["period_start"] == current["start"]
        and row["period_end"] == current["end"] for row in result["items"]
    ))
    own = scope == "self" or (scope == "person" and str(user_id) == actor.user_id)
    checks.require("current_target_editability", result["editable"] is own)
    history = await read_targets(
        connection, actor, period_type="quarter", anchor_date=current["start"] - timedelta(days=1),
        scope=scope, user_id=user_id, team_id=team_id,
    )
    checks.require("historical_target_readonly", history["editable"] is False)
    return {row["kind"]: row["amount"] for row in result["items"] if row["kind"] in KINDS}


async def check_sales_view(connection, actor, checks, current, *, scope="self", member_id=None, team_id=None):
    await set_request_context(connection, actor)
    result = await performance(
        connection, actor, scope=scope, member_id=member_id, team_id=team_id,
        year=current["year"], quarter=current["quarter"],
    )
    checks.require("quarter_performance_period", result["target_period"] == current)
    selected = result["selected_subject"]
    checks.require("stable_selected_subject", selected["member_id"] == (
        actor.user_id if scope == "self" else member_id
    ) and selected["team_id"] == team_id)
    targets = await check_targets(
        connection, actor, checks, current, scope=scope, user_id=member_id, team_id=team_id,
    )
    checks.require("profile_target_values", result["targets"] == targets)
    own = scope == "self" or (scope == "person" and member_id == actor.user_id)
    checks.require("profile_editability", result["editable"] is own and selected["editable"] is own)
    previous = current["start"] - timedelta(days=1)
    historical = await performance(
        connection, actor, scope=scope, member_id=member_id, team_id=team_id,
        year=previous.year, quarter=(previous.month - 1) // 3 + 1,
    )
    checks.require("historical_profile_readonly", historical["editable"] is False)
    checks.require("independent_efficiency_window", result["efficiency"] == historical["efficiency"])
    for key in ("customers", "opportunities"):
        check_ratio(checks, result["efficiency"][key])
    followup = result["efficiency"]["followup"]
    checks.require("followup_counts", 0 <= followup["customer_count"] <= followup["count"]
                   and 0 <= followup["evaluated_count"] <= followup["count"])
    mapped = await map_customers(
        scope=scope, member_id=member_id, member_ids=[], team_id=team_id,
        identity=SimpleNamespace(actor=actor), database=ExistingReadonlyTransaction(connection),
    )
    rows = mapped["items"]
    customers = result["efficiency"]["customers"]
    checks.require("map_unique_customers", len({row["id"] for row in rows}) == len(rows))
    checks.require("map_ratio_denominator", len(rows) == customers["denominator"])
    checks.require("map_ratio_numerator", sum(
        row["quadrant_code"] in {"main_attack", "customer_asset"} for row in rows
    ) == customers["numerator"])
    return result


async def check_fde_view(connection, actor, checks, current, *, scope="self", member_id=None, team_id=None):
    await set_request_context(connection, actor)
    result = await fde_dashboard(
        connection, actor, scope=scope, member_id=member_id, team_id=team_id,
        year=current["year"], quarters=[current["quarter"]], period="quarter",
    )
    checks.require("fde_quarter_actual_period", result["filters"]["date_from"] == current["start"]
                   and result["filters"]["date_to"] == current["end"])
    checks.require("fde_stable_team", result["filters"]["team_id"] == team_id)
    target_scope = "person" if member_id else scope
    await check_targets(
        connection, actor, checks, current, scope=target_scope, user_id=member_id, team_id=team_id,
    )
    portrait = await get_profile(connection, actor, 30, scope=scope, member_id=member_id, team_id=team_id)
    checks.require("fde_profile_read", portrait["data_source"] == "database")
    if team_id:
        checks.require("fde_team_profile_readonly", portrait["can_review"] is False
                       and portrait["member_id"] is None)
    return result


async def load_actors(connection, checks):
    identity = IdentityRepository()
    admin = await identity.find_maintenance_administrator(
        connection, workspace_external_id="demo-sales-workspace", account_code="OPSADMIN",
    )
    checks.require("maintenance_identity_available", admin is not None)
    await set_request_context(connection, admin.context)
    people = await connection.fetch(
        """SELECT DISTINCT u.id::text,b.role_code FROM platform.user_ref u
        JOIN platform.role_binding b ON b.user_ref_id=u.id AND b.workspace_id=u.workspace_id
        WHERE u.workspace_id=$1::uuid AND u.status='active' AND u.deleted_at IS NULL
          AND b.role_code=ANY($2::text[])
          AND clock_timestamp()>=b.valid_from AND clock_timestamp()<b.valid_to
        ORDER BY b.role_code,u.id::text""", admin.context.workspace_id, list(ROLES),
    )
    actors = {}
    for row in people:
        record = await identity.find_actor_by_id(
            connection, workspace_id=admin.context.workspace_id, user_id=row["id"], role=row["role_code"],
        )
        if record:
            actors[(row["id"], row["role_code"])] = record.context
    checks.require("all_business_roles_available", {role for _, role in actors} == set(ROLES))
    return actors


async def verify(connection, checks):
    checks.require("runtime_enforces_rls", await connection.fetchval(
        "SELECT NOT (rolsuper OR rolbypassrls) FROM pg_roles WHERE rolname=current_user"
    ))
    checks.require("transaction_readonly", await connection.fetchval("SHOW transaction_read_only") == "on")
    actors = await load_actors(connection, checks)
    now = today()
    current = quarter_period(now.year, (now.month - 1) // 3 + 1)
    personal = {}
    for (user_id, role), actor in actors.items():
        check_view = check_sales_view if role in SALES_ROLES else check_fde_view
        personal[(user_id, role)] = await check_view(connection, actor, checks, current)

    for (_, role), actor in actors.items():
        await set_request_context(connection, actor)
        if role in SALES_ROLES:
            directory = await scope_options(connection, actor)
            checks.require("sales_directory_role_bounds", all(m["role"] in SALES_ROLES for m in directory["members"]))
            if role == "sales":
                checks.require("sales_self_only", {m["id"] for m in directory["members"]} == {actor.user_id}
                               and not directory["teams"])
                continue
            for member in directory["members"]:
                if member["role"] != "sales":
                    continue
                viewed = await check_sales_view(
                    connection, actor, checks, current, scope="person", member_id=member["id"],
                )
                own = personal[(member["id"], "sales")]
                checks.require("selected_sales_matches_self", all(
                    viewed[key] == own[key] for key in ("actuals", "targets", "efficiency")
                ))
            for team in directory["teams"]:
                await check_sales_view(connection, actor, checks, current, scope="team", team_id=team["id"])
            if role == "manager":
                await check_sales_view(connection, actor, checks, current, scope="department")
        else:
            directory = await fde_scope_options(connection, actor)
            checks.require("fde_directory_role_bounds", all(
                (member["id"], "fde") in actors or (member["id"], "fde_lead") in actors
                for member in directory["members"]
            ))
            if role == "fde":
                checks.require("fde_self_only", directory["allowed_scopes"] == ["self"] and not directory["teams"])
                continue
            for member in directory["members"]:
                if (member["id"], "fde") not in personal:
                    continue
                viewed = await check_fde_view(connection, actor, checks, current, scope="team", member_id=member["id"])
                own = personal[(member["id"], "fde")]
                checks.require("selected_fde_matches_self", all(
                    viewed["summary"][key] == own["summary"][key]
                    for key in ("recognized_amount", "collection_amount", "period_visits", "period_customers")
                ))
            for team in directory["teams"]:
                await check_fde_view(connection, actor, checks, current, scope="team", team_id=team["id"])
    return Counter(role for _, role in actors)


async def main():
    db = Database(get_settings())
    checks = Checks()
    try:
        await db.connect()
        async with db.connection() as connection:
            async with connection.transaction(readonly=True, isolation="repeatable_read"):
                roles = await verify(connection, checks)
    finally:
        await db.close()
    print(json.dumps({
        "ok": True, "readonly": True, "business_writes": False, "model_calls": False,
        "sessions_created": False, "roles": roles, "check_counts": checks.counts,
        "checks_passed": sum(checks.counts.values()),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(json.dumps({"ok": False, "readonly": True, "error_type": type(error).__name__}))
        raise SystemExit(1) from None
