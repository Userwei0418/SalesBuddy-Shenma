"""Map range HTTP, scope and pagination contracts; no database or model calls.

SQL assertions below inspect captured SQL only. Real RLS/query execution remains
an integration acceptance step and is not established by these mocks.
"""
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from sales_backend.api import customer_assets, permission_gate
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.domain.activity_window import activity_window_bounds, map_activity_window_bounds
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.main import app
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.customer_map import CustomerMapRepository
from sales_backend.services import customer_map

AS_OF = date(2026, 10, 13)
ACTOR = ActorContext(workspace_id="00000000-0000-0000-0000-000000000001",
                     user_id="00000000-0000-0000-0000-000000000002",
                     role=RoleCode.SALES, data_scope=DataScope.SELF)


def point(number, *, scored=False):
    return {"id": f"10000000-0000-0000-0000-{number:012d}", "name": f"合成客户{number}",
            "potential_score": 0 if scored else None, "relationship_score": 50 if scored else None,
            "assessment_id": f"20000000-0000-0000-0000-{number:012d}" if scored else None,
            "latest_visit_at": None, "weekly_follow_up_count": 0}


@pytest.fixture
def map_api(monkeypatch):
    connection = SimpleNamespace(execute=AsyncMock(), fetch=AsyncMock(return_value=[]),
                                 fetchval=AsyncMock(return_value=True))
    transactions = []

    @asynccontextmanager
    async def transaction(actor, readonly=False):
        assert actor == ACTOR
        transactions.append(readonly)
        yield connection

    database = SimpleNamespace(transaction=transaction)
    identity = SimpleNamespace(actor=ACTOR, client_channel="wechat-mini-program", auth_method="password")
    snapshot = {"workspace_id": ACTOR.workspace_id, "user_id": ACTOR.user_id, "grants": [
        {"permission_code": permission, "scope_code": "workspace" if permission.startswith("access.") else "self", "team_ids": [],
         "effect": "allow", "source_code": "synthetic", "source_id": "fixture"}
        for permission in ("access.mini_program", "battle_map.read")
    ]}
    snapshot_call = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(AuthorizationRepository, "snapshot", snapshot_call)
    monkeypatch.setattr(permission_gate, "get_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(app.state, "database", database, raising=False)
    monkeypatch.setattr(app.state, "settings", SimpleNamespace(), raising=False)
    monkeypatch.setattr(customer_assets, "today", lambda: AS_OF)
    read = AsyncMock(return_value=[])
    monkeypatch.setattr(customer_assets, "read_map", read)
    mutation = AsyncMock(side_effect=AssertionError("unexpected mutation"))
    monkeypatch.setattr(customer_assets, "execute_mutation", mutation)
    overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_database] = lambda: database
    app.dependency_overrides[get_identity] = lambda: identity
    try:
        yield SimpleNamespace(connection=connection, transactions=transactions, database=database,
                              identity=identity, snapshot=snapshot, snapshot_call=snapshot_call,
                              read=read, mutation=mutation)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


async def request(method="GET", suffix="", **params):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://map.invalid") as client:
        return await client.request(method, "/api/v1/customer-assets/map" + suffix, params=params)


@pytest.mark.parametrize("as_of,start", [
    (date(2026, 1, 1), date(2026, 1, 1)), (date(2024, 2, 29), date(2024, 1, 1)),
    (date(2026, 3, 31), date(2026, 1, 1)), (date(2026, 4, 1), date(2026, 4, 1)),
    (date(2026, 7, 1), date(2026, 7, 1)), (date(2026, 10, 1), date(2026, 10, 1)),
    (date(2026, 12, 31), date(2026, 10, 1)),
])
def test_map_quarter_is_inclusive_calendar_quarter(as_of, start):
    bounds = map_activity_window_bounds(as_of, "quarter")
    assert (bounds.key, bounds.start, bounds.end) == ("quarter", start, as_of)


def test_map_all_is_unbounded_and_other_consumers_do_not_gain_new_windows():
    bounds = map_activity_window_bounds(AS_OF, "all")
    assert (bounds.key, bounds.start, bounds.end) == ("all", None, None)
    for window in ("all", "quarter"):
        with pytest.raises(ValueError):
            activity_window_bounds(AS_OF, window)
    for window in (None, "today", "day", "3d", "week", "month", "year"):
        old, new = activity_window_bounds(AS_OF, window), map_activity_window_bounds(AS_OF, window)
        assert (old.key, old.start, old.end) == (new.key, new.start, new.end)


@pytest.mark.asyncio
@pytest.mark.parametrize("window,start,limit,all_customers", [
    (None, date(2026, 1, 1), None, False), ("all", None, 200, True),
    ("quarter", date(2026, 10, 1), 200, False),
])
async def test_repository_preserves_scope_and_pages_before_related_projection(window, start, limit, all_customers):
    connection = SimpleNamespace(fetch=AsyncMock(return_value=[point(1)]))
    rows = await CustomerMapRepository().read(
        connection, as_of=AS_OF, activity_window=window, fde_user_ids=[ACTOR.user_id],
        subject_member_ids=[ACTOR.user_id], subject_scope="person", subject_team_ids=[], explicit_subject=True,
    )
    query, *args = connection.fetch.await_args.args
    assert rows == [point(1)]
    assert args == [[ACTOR.user_id], start, AS_OF, [ACTOR.user_id], "person", [], True, all_customers, limit, 0, "all"]
    assert "security.authorization_portfolio_customer('battle_map.read',c.id)" in query
    assert "($8::boolean OR EXISTS (" in query
    assert "SELECT 1 FROM active_customers active WHERE active.customer_id=c.id" in query
    assert "'customer'=ANY(c.business_identities)" in query
    assert "WHERE $1::uuid[] IS NULL OR c.claimant_id=ANY($1::uuid[])" in query
    assert "score.workspace_id=c.workspace_id" in query and "score.subject_user_ref_id=ANY($4::uuid[])" in query
    assert query.index("authorization_portfolio_customer") < query.index("page_customers AS MATERIALIZED")
    assert query.index("WHERE $1::uuid[] IS NULL OR c.claimant_id=ANY($1::uuid[])") < query.index("authorized AS MATERIALIZED")
    assert "SELECT c.* FROM scoped_customers c\n              WHERE security.authorization_portfolio_customer" in query
    assert "SELECT c.* FROM authorized c\n              LEFT JOIN active_customers" in query
    assert query.index("LIMIT $9::integer OFFSET $10::bigint") < query.index("FROM insight.quadrant_score")
    assert query.index("LIMIT $9::integer OFFSET $10::bigint") < query.index(" AS selects_subject")
    assert "FROM page_customers c\n            )" in query
    assert "FROM scored_customers c\n            LEFT JOIN active_customers" in query
    assert "LEFT JOIN active_customers active ON active.customer_id=c.id" in query
    assert "COALESCE(active.weekly_follow_up_count,0)" in query


@pytest.mark.asyncio
async def test_all_read_still_resolves_permission_filtered_member_directory(monkeypatch):
    permissions = SimpleNamespace(require=Mock())
    effective = AsyncMock(return_value=permissions)
    monkeypatch.setattr(AuthorizationRepository, "effective", effective)
    teams = AsyncMock(return_value=[])
    monkeypatch.setattr(customer_map, "selectable_teams", teams)
    scopes = AsyncMock(return_value=("person", [ACTOR.user_id], None))
    directory = AsyncMock(return_value={"members": [{"id": ACTOR.user_id}, {"id": "other"}], "teams": []})
    read = AsyncMock(return_value=[])
    monkeypatch.setattr(customer_map, "scope_members", scopes)
    monkeypatch.setattr(customer_map, "scope_subjects", directory)
    monkeypatch.setattr(CustomerMapRepository, "read", read)
    await customer_map.read_map(object(), ACTOR, as_of=AS_OF, activity_window="all", scope="self",
                                row_limit=201, offset=200)
    effective.assert_awaited_once()
    teams.assert_awaited_once()
    permissions.require.assert_called_once_with("battle_map.read")
    assert scopes.await_args.kwargs["permissions"] is directory.await_args.kwargs["permissions"] is permissions
    assert scopes.await_args.kwargs["teams"] is directory.await_args.kwargs["teams"] is teams.return_value
    assert scopes.await_args.kwargs["permission"] == directory.await_args.kwargs["permission"] == "battle_map.read"
    assert read.await_args.kwargs == dict(fde_user_ids=[ACTOR.user_id], as_of=AS_OF, activity_window="all",
                                         subject_member_ids=[ACTOR.user_id], subject_scope="person",
                                         subject_team_ids=[], explicit_subject=True, row_limit=201, offset=200, business_identity="all")


@pytest.mark.asyncio
async def test_map_rechecks_permissions_on_each_read_and_stops_before_projection_when_denied(monkeypatch):
    from sales_backend.domain.authorization import Authorization

    effective = AsyncMock(return_value=Authorization.combine(ACTOR.workspace_id, ACTOR.user_id, []))
    monkeypatch.setattr(AuthorizationRepository, "effective", effective)
    projection = AsyncMock()
    monkeypatch.setattr(CustomerMapRepository, "read", projection)
    for _ in range(2):
        with pytest.raises(PermissionError):
            await customer_map.read_map(object(), ACTOR, as_of=AS_OF, activity_window="all")
    assert effective.await_count == 2
    projection.assert_not_awaited()


@pytest.mark.asyncio
async def test_map_subjects_preserve_directory_members_without_loading_selector_metadata(monkeypatch):
    from sales_backend.repositories import profile_scope

    members = [{"id": ACTOR.user_id, "display_name": "Synthetic", "role": "sales"}]
    teams = [{"id": "team", "name": "Synthetic"}]
    permissions = SimpleNamespace(require=Mock())
    connection = SimpleNamespace(fetch=AsyncMock(return_value=members), fetchval=AsyncMock())
    attach = AsyncMock(side_effect=AssertionError("map must not load selector metadata"))
    monkeypatch.setattr(profile_scope, "attach_member_teams", attach)
    result = await profile_scope.scope_subjects(connection, ACTOR, permission="battle_map.read",
                                               permissions=permissions, teams=teams)
    assert result == {"members": members, "teams": teams}
    permissions.require.assert_called_once_with("battle_map.read")
    connection.fetch.assert_awaited_once()
    connection.fetchval.assert_not_awaited()
    attach.assert_not_awaited()


@pytest.mark.asyncio
async def test_all_page_keeps_null_scores_and_reports_only_page_coverage(map_api):
    original = [point(1, scored=True), point(2), point(3)]
    map_api.read.return_value = original
    response = await request(activity_window="all", page_size=2, offset=200)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["activity_start"] is body["activity_end"] is body["activity_since"] is None
    assert body["as_of"] == AS_OF.isoformat() and body["activity_window"] == "all"
    assert body["map_status"] == "partial" and body["refresh_allowed"] is False
    assert body["coverage"] == {"returned_count": 2, "scored_count": 1, "unassessed_count": 1}
    assert body["returned_count"] == body["active_customer_count"] == body["page_size"] == 2
    assert body["offset"] == 200 and body["has_more"] is True and body["next_offset"] == 202
    assert "total" not in body and "total_count" not in body
    assert body["items"] == [{**original[0], "assessment_status": "assessed"},
                              {**original[1], "assessment_status": "unassessed"}]
    assert "assessment_status" not in original[0]
    assert map_api.read.await_args.kwargs["row_limit"] == 3
    assert map_api.read.await_args.kwargs["offset"] == 200
    assert map_api.transactions == [True, True]
    assert not map_api.mutation.mock_calls and not map_api.connection.execute.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [point(1, scored=True)]])
async def test_all_empty_and_fully_assessed_last_page_are_ready(map_api, rows):
    map_api.read.return_value = rows
    body = (await request(activity_window="all")).json()
    assert body["map_status"] == "ready" and body["refresh_allowed"] is False
    assert body["page_size"] == 200 and body["has_more"] is False and body["next_offset"] is None
    assert map_api.read.await_args.kwargs["row_limit"] == 201


@pytest.mark.asyncio
@pytest.mark.parametrize("window,start,limit", [
    (None, "2026-01-01", None), ("quarter", "2026-10-01", 200), ("day", "2026-10-13", None),
])
async def test_old_defaults_and_quarter_keep_active_recovery_contract(map_api, window, start, limit):
    map_api.read.return_value = [point(1)]
    body = (await request(**({"activity_window": window} if window else {}))).json()
    assert body["activity_window"] == ("today" if window == "day" else window or "year")
    assert body["activity_start"] == body["activity_since"] == start
    assert body["activity_end"] == "2026-10-13"
    assert body["page_size"] == limit and body["map_status"] == "updating" and body["refresh_allowed"] is True
    assert map_api.read.await_args.kwargs["row_limit"] == (limit + 1 if limit else None)


@pytest.mark.asyncio
async def test_legacy_direct_handler_defaults_are_plain_values(map_api):
    result = await customer_assets.map_customers(scope=None, member_id=None, member_ids=[], team_id=None,
                                                identity=map_api.identity, database=map_api.database)
    assert result["activity_window"] == "year" and result["page_size"] is None and result["offset"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("method,suffix", [("GET", ""), ("POST", "/refresh")])
@pytest.mark.parametrize("invalid", [
    {"page_size": 0}, {"page_size": 501}, {"offset": -1}, {"offset": 2147483648},
    {"activity_window": "unknown"}, {"scope": "department"},
])
async def test_invalid_page_or_window_never_reaches_handler(map_api, method, suffix, invalid):
    response = await request(method, suffix, **invalid)
    assert response.status_code == 422 and response.headers["cache-control"] == "no-store"
    assert not map_api.read.mock_calls and not map_api.mutation.mock_calls
    assert map_api.transactions == [True]  # Only the real permission gate snapshot.


@pytest.mark.asyncio
async def test_maximum_page_uses_one_extra_row_for_has_more(map_api):
    map_api.read.return_value = [point(i) for i in range(501)]
    body = (await request(activity_window="all", page_size=500)).json()
    assert len(body["items"]) == 500 and body["next_offset"] == 500
    assert map_api.read.await_args.kwargs["row_limit"] == 501


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", ["access.mini_program", "battle_map.read"])
async def test_all_refresh_cannot_bypass_real_route_permission_gate(map_api, monkeypatch, denied):
    map_api.snapshot["grants"] = [g for g in map_api.snapshot["grants"] if g["permission_code"] != denied]
    refresh = AsyncMock(side_effect=AssertionError("handler must not run"))
    monkeypatch.setattr(customer_assets, "refresh_missing_map", refresh)
    response = await request("POST", "/refresh", activity_window="all")
    assert response.status_code == 403
    assert map_api.transactions == [True] and not refresh.mock_calls and not map_api.mutation.mock_calls


@pytest.mark.asyncio
async def test_all_refresh_without_authentication_is_rejected(map_api, monkeypatch):
    monkeypatch.setattr(permission_gate, "get_identity", get_identity)
    response = await request("POST", "/refresh", activity_window="all")
    assert response.status_code == 401
    assert map_api.transactions == [] and not map_api.mutation.mock_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_denied", [False, True])
async def test_all_refresh_checks_scope_readonly_and_never_locks_or_enqueues(map_api, monkeypatch, scope_denied):
    read = AsyncMock(return_value=[point(1)], side_effect=PermissionError("scope denied") if scope_denied else None)
    enqueue = AsyncMock(side_effect=AssertionError("all cannot enqueue"))
    monkeypatch.setattr(customer_map, "read_map", read)
    monkeypatch.setattr(customer_map, "enqueue_battle_map_review", enqueue)
    response = await request("POST", "/refresh", activity_window="all", scope="self", page_size=200, offset=200)
    assert response.status_code == (403 if scope_denied else 200), response.text
    assert map_api.transactions == [True, True]
    assert read.await_count == 1 and read.await_args.kwargs["scope"] == "self"
    assert read.await_args.kwargs["row_limit"] == 1
    assert not map_api.connection.execute.mock_calls and not map_api.connection.fetch.mock_calls
    assert not enqueue.mock_calls and not map_api.mutation.mock_calls
    if not scope_denied:
        assert response.json() == {"status": "unavailable", "enqueued_count": 0, "refresh_allowed": False,
                                   "code": "BATTLE_MAP_ALL_REFRESH_DISABLED",
                                   "reason": "全部范围仅展示已有评分；本页未评估客户不会自动补评，请选择活动时间范围后更新"}


@pytest.mark.asyncio
@pytest.mark.parametrize("params,expected_limit,expected_offset", [
    ({}, None, 0), ({"activity_window": "quarter"}, 200, 0),
    ({"activity_window": "quarter", "page_size": 2, "offset": 200}, 2, 200),
])
async def test_refresh_fingerprint_and_jobs_follow_exact_selected_page(map_api, monkeypatch, params,
                                                                    expected_limit, expected_offset):
    read = AsyncMock(return_value=[point(202, scored=True), point(203)])
    enqueue = AsyncMock()
    monkeypatch.setattr(customer_map, "read_map", read)
    monkeypatch.setattr(customer_map, "enqueue_battle_map_review", enqueue)

    async def mutation(connection, actor, key, operation, payload, callback):
        assert operation == "battle_map.refresh"
        expected = {"activity_window": params.get("activity_window", "year"), "scope": None,
                    "member_id": None, "member_ids": [], "team_id": None}
        if expected_limit is not None:
            expected.update(page_size=expected_limit, offset=expected_offset)
        assert payload == expected  # Old callers retain their original idempotency fingerprint.
        return await callback()

    map_api.mutation.side_effect = mutation
    response = await request("POST", "/refresh", **params)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "updating", "enqueued_count": 1}
    assert read.await_args.kwargs["row_limit"] == expected_limit
    assert read.await_args.kwargs["offset"] == expected_offset
    assert map_api.connection.fetch.await_args.args[2] == [point(203)["id"]]
    assert enqueue.await_count == 1 and enqueue.await_args.kwargs["customer_id"] == point(203)["id"]
    assert enqueue.await_args.args[1] == ACTOR
    assert map_api.transactions == [True, False]
