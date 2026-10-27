"""Review snapshot provenance, incomplete values and independent live guards."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from sales_backend.contracts.work_report import ResolvedScope, SnapshotSelection
from sales_backend.contracts.work_report_base import PeriodSelection
from sales_backend.domain.work_report import WorkReportError, resolve_period
from sales_backend.services import work_report_review_facts as service


@pytest.fixture
def context(monkeypatch):
    actor = SimpleNamespace(workspace_id=str(uuid4()), user_id=str(uuid4()), team_ids=[])
    now = datetime(2026, 10, 14, 4, tzinfo=UTC)
    scope = ResolvedScope(kind="self", label="本人", member_ids=[actor.user_id])
    selection = SnapshotSelection(period=PeriodSelection(kind="day", anchor_date=now.date()), review_mode="daily")
    period = resolve_period(selection.period, now=now)
    card = dict(
        metric_code="forecast_collection",
        definition_version=1,
        unit="元",
        currency="CNY",
        tax_basis="cash_collection_not_applicable",
        value="0.00",
        known_subtotal="0.00",
        coverage="complete",
        missing_reasons=[],
        source_refs=[],
        target=dict(status="no_target"),
        gap=None,
        rate=None,
    )
    projection = dict(period=period, scope=scope, data_as_of=now.isoformat(), metrics=[card], quarter={"year": 2026})
    material = {key: [] for key in service.ACTIVITY_LABELS}
    counts = {key: dict(value=0, known_count=0, coverage="complete", missing_reasons=[]) for key in material}
    connection = AsyncMock()
    connection.is_in_transaction = Mock(return_value=True)
    sql = []

    async def fetchval(query, *args):
        sql.append((query, args))
        return now if query == "SELECT transaction_timestamp()" else True

    async def fetchrow(query, *args):
        row = dict(workspace_id=actor.workspace_id, user_id=actor.user_id, rls="on")
        if "transaction_isolation" in query:
            row.update(isolation="repeatable read", readonly="on")
        return row

    connection.fetchval.side_effect = fetchval
    connection.fetchrow.side_effect = fetchrow
    connection.fetch.return_value = []
    metric_mock = AsyncMock(return_value=projection)
    activity_mock = AsyncMock(return_value=(material, counts, []))
    kr_mock = AsyncMock(return_value=dict(items=[], total=0))
    monkeypatch.setattr(service.review_overview, "read_metric_projection", metric_mock)
    monkeypatch.setattr(service.review_overview, "activity_material", activity_mock)
    monkeypatch.setattr(service.review_okr, "read_key_results", kr_mock)
    return SimpleNamespace(
        actor=actor,
        now=now,
        scope=scope,
        selection=selection,
        period=period,
        card=card,
        projection=projection,
        material=material,
        counts=counts,
        connection=connection,
        sql=sql,
        kr_mock=kr_mock,
        metric_mock=metric_mock,
        activity_mock=activity_mock,
    )


async def prepare(c, **changes):
    return await service.prepare_review_facts(
        c.connection,
        c.actor,
        **(dict(
            selection=c.selection,
            scope=c.scope,
            period=c.period,
            data_as_of=c.now,
            permission_version="p1",
            mode="daily",
        ) | changes),
    )


def kr(c, **changes):
    return dict(
        id=str(uuid4()),
        objective_id=str(uuid4()),
        title="季度进展",
        status="active",
        version_no=2,
        metric_code="forecast_collection",
        metric=dict(code="forecast_collection", unit="元", definition_version=1),
        scope=dict(scope_type="person", user_id=c.actor.user_id, team_id=None, department_code="sales"),
        period=dict(start="2026-10-01", end="2026-12-31"),
        progress_mode="automatic",
        progress={**c.card, "data_as_of": c.now.isoformat()},
        **changes,
    )


@pytest.mark.asyncio
async def test_exact_zero_uses_scope_dependency_and_no_fake_baseline(context):
    facts, deps = await prepare(context)
    forecast = next(m for m in facts.measures if m.role == "forecast")
    change = next(m for m in facts.measures if m.role == "day_change")
    assert forecast.value == "0.00" and forecast.coverage == "complete"
    assert change.value is None and change.baseline is None and change.missing_reasons == ["no_baseline"]
    assert change.comparison_boundary == context.period.start
    subject = next(d for d in deps if d["source_type"] == "review_subject")
    assert subject["record_id"] == context.actor.user_id
    assert subject["required_permissions"] == ["customer.read", "opportunity.read", "work_report.read"]
    assert facts.data_as_of == context.now and facts.period == context.period


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [timedelta(seconds=2), timedelta(days=1)])
async def test_queued_report_preserves_api_cutoff_and_implicit_anchor(context, delay):
    requested_at = context.now - delay
    selection = SnapshotSelection(period=PeriodSelection(kind="day"), review_mode="daily")
    frozen_period = resolve_period(selection.period, now=requested_at)
    context.projection["period"] = resolve_period(
        PeriodSelection(kind="day", anchor_date=frozen_period.cutoff.date()), now=context.now
    )
    facts, _ = await prepare(context, selection=selection, period=frozen_period)
    assert facts.period == frozen_period and facts.data_as_of == context.now
    assert context.metric_mock.await_args.args[2].anchor_date == frozen_period.cutoff.date()
    assert context.activity_mock.await_args.args[2]["period"] == frozen_period


@pytest.mark.asyncio
async def test_queued_report_rejects_cutoff_after_current_projection(context):
    future = context.period.model_copy(update={"cutoff": context.now + timedelta(seconds=1)})
    with pytest.raises(WorkReportError, match="周期或授权范围"):
        await prepare(context, period=future)


@pytest.mark.asyncio
async def test_partial_keeps_known_subtotal_only_in_evidence(context):
    context.card.update(value=None, known_subtotal="17.20", coverage="partial", missing_reasons=["missing_amount"])
    facts, deps = await prepare(context)
    forecast = next(m for m in facts.measures if m.role == "forecast")
    assert forecast.value is None and "missing_amount" in facts.missing_data
    query = next(e for e in facts.evidence if e.kind == "metric_query")
    assert json.loads(query.text)["known_subtotal"] == "17.20"
    assert next(d for d in deps if d["source_type"] == "review_subject")["required_permissions"] == ["work_report.read"]


@pytest.mark.asyncio
async def test_partial_forecast_keeps_individual_customer_dependency(context):
    opportunity, customer = str(uuid4()), str(uuid4())
    context.card.update(
        value=None,
        known_subtotal="12.34",
        coverage="partial",
        missing_reasons=["scope_partial"],
        source_refs=[dict(opportunity_id=opportunity, opportunity_version=2, forecast_id=str(uuid4()))],
    )
    context.connection.fetch.return_value = [dict(id=opportunity, customer_id=customer)]
    facts, deps = await prepare(context)
    assert any(d["source_type"] == "meeting_customer" and d["record_id"] == customer for d in deps)
    assert next(m for m in facts.measures if m.role == "forecast").value is None


@pytest.mark.asyncio
async def test_unavailable_financial_kr_preserves_tax_contract(context):
    row = kr(context)
    row.update(
        metric_code="recognized",
        metric=dict(unit="元", definition_version=1, source_kind="actual"),
        progress=dict(value=None, coverage="unavailable", missing_reasons=["actual_source_permission_denied"]),
    )
    context.kr_mock.return_value = dict(items=[row], total=1)
    facts, _ = await prepare(context)
    assert all(
        m.tax_basis == "tax_exclusive" and m.value is None for m in facts.measures if m.metric_code == "recognized"
    )


@pytest.mark.asyncio
async def test_collection_refuses_implicit_separate_transactions(context):
    context.connection.is_in_transaction.return_value = False
    with pytest.raises(WorkReportError):
        await prepare(context)


@pytest.mark.asyncio
async def test_effective_target_only_and_tax_basis(context):
    target = str(uuid4())
    context.card.update(
        metric_code="forecast_recognized",
        tax_basis="tax_inclusive",
        target=dict(id=target, version_no=3, value="90.00", status="effective"),
        gap="90.00",
        rate="0.00000000",
    )
    facts, deps = await prepare(context)
    assert all(m.tax_basis == "tax_inclusive" for m in facts.measures)
    goal = next(m for m in facts.measures if m.role == "effective_target")
    assert goal.value == "90.00"
    evidence = {e.source_ref: e for e in facts.evidence}
    assert all(evidence[r].target_status == "effective" for r in goal.source_refs)
    assert any(d["source_type"] == "review_target" and d["record_id"] == target for d in deps)


@pytest.mark.asyncio
async def test_task_declarations_group_but_never_supply_measures(context):
    task, kr_id = str(uuid4()), str(uuid4())
    context.material["completed_tasks"] = [dict(id=task, task_id=task, name="回访", member_ids=[], source_refs=[])]
    context.connection.fetch.return_value = [
        dict(
            id=task,
            version_no=2,
            alignment=dict(
                metric_codes=["forecast_collection", "daily_routine"],
                key_result_ids=[kr_id],
                expected_contribution={"metric_code": "forecast_collection", "value": "800.00"},
                actual_contribution={"metric_code": "forecast_collection", "value": "700.00"},
                restricted_key_result_count=0,
            ),
        )
    ]
    row = kr(context)
    row["id"] = kr_id
    context.kr_mock.return_value = dict(items=[row], total=1)
    facts, deps = await prepare(context)
    task_evidence = [e for e in facts.evidence if e.kind == "task"]
    assert {e.state_kind for e in task_evidence} == {"occurred", "planned", "declaration"}
    assert all(set(m.source_refs).isdisjoint(e.source_ref for e in task_evidence) for m in facts.measures)
    group = next(g for g in facts.groups if g.group_id == "kr:" + kr_id)
    assert {e.source_ref for e in task_evidence} <= set(group.source_refs)
    assert all(m.value in (None, "0.00") for m in facts.measures)
    assert sum(d["source_type"] == "task" for d in deps) == 1


@pytest.mark.asyncio
async def test_legacy_unaligned_task_is_not_given_financial_or_routine_category(context):
    task = str(uuid4())
    context.material["pending_tasks"] = [dict(id=task, task_id=task, name="旧任务", member_ids=[])]
    context.connection.fetch.return_value = [dict(id=task, version_no=1, alignment=None)]
    facts, _ = await prepare(context)
    ref = next(e.source_ref for e in facts.evidence if e.kind == "task")
    assert all(ref not in g.source_refs for g in facts.groups)
    assert "task_alignment_missing" in facts.missing_data


@pytest.mark.asyncio
async def test_distinct_visits_to_same_customer_have_distinct_audit_identities(context):
    customer = str(uuid4())
    ids = [str(uuid4()), str(uuid4())]
    context.material["formal_visits"] = [dict(id=i, customer_id=customer, name="跟进", version_no=1) for i in ids]
    facts, deps = await prepare(context)
    assert {e.record_id for e in facts.evidence if e.kind == "activity"} == set(ids)
    assert {d["record_id"] for d in deps if d["source_type"] == "visit"} == set(ids)


@pytest.mark.asyncio
async def test_key_results_exhaust_pagination_and_preserve_same_card_coverage(context):
    context.card.update(value=None, known_subtotal="10.00", coverage="partial", missing_reasons=["missing_amount"])
    rows = [kr(context), kr(context)]
    context.kr_mock.side_effect = [dict(items=[rows[0]], total=2), dict(items=[rows[1]], total=2)]
    facts, _ = await prepare(context)
    assert [call.kwargs["offset"] for call in context.kr_mock.call_args_list] == [0, 1]
    progress = [m for m in facts.measures if m.role == "kr_progress"]
    assert len(progress) == 2 and all(m.value is None and m.coverage == "partial" for m in progress)


@pytest.mark.asyncio
async def test_confirmed_manual_nonfinancial_progress_is_explicit(context):
    row = kr(context)
    row.update(
        metric_code="custom_actions",
        metric=dict(unit="次", definition_version=1),
        progress_mode="manual",
        progress=dict(value="4", version_no=2, status="confirmed", evidence="逐项核对", source_ref=str(uuid4())),
    )
    context.kr_mock.return_value = dict(items=[row], total=1)
    facts, _ = await prepare(context)
    progress = next(m for m in facts.measures if m.role == "kr_progress")
    assert progress.value == "4" and progress.metric_code == "custom_actions"
    evidence = next(e for e in facts.evidence if e.source_ref in progress.source_refs)
    assert json.loads(evidence.text)["confirmed_progress"]["evidence"] == "逐项核对"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timestamp", "period", "scope", "kr_pagination", "kr_timestamp", "unknown_source"])
async def test_inconsistent_snapshot_is_never_frozen(context, failure):
    if failure == "timestamp":
        context.now += timedelta(seconds=1)
    elif failure == "period":
        context.projection["period"] = context.period.model_copy(update={"label": "different"})
    elif failure == "scope":
        context.projection["scope"] = context.scope.model_copy(update={"member_ids": [uuid4()]})
    elif failure == "kr_pagination":
        context.kr_mock.return_value = dict(items=[], total=1)
    elif failure == "kr_timestamp":
        row = kr(context)
        row["progress"]["data_as_of"] = (context.now - timedelta(seconds=1)).isoformat()
        context.kr_mock.return_value = dict(items=[row], total=1)
    else:
        context.card["source_refs"] = [dict(kind="unverified", id=str(uuid4()))]
    with pytest.raises(WorkReportError):
        await prepare(context)


@pytest.mark.asyncio
async def test_different_current_reader_is_authorized_against_frozen_owner(context):
    facts, deps = await prepare(context)
    context.actor.user_id = str(uuid4())
    context.sql.clear()
    await service.assert_review_access(
        context.connection, context.actor, facts, dependencies=deps, permission="work_report.share"
    )
    subject_calls = [args for query, args in context.sql if query.startswith("SELECT security.authorization_subject")]
    assert ("work_report.share", "person", str(facts.scope.member_ids[0]), None) in subject_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["manifest", "subject", "opportunity", "visit", "task", "target", "key_result"])
async def test_current_revocation_blocks_historical_facts_without_version_comparison(context, resource):
    facts, deps = await prepare(context)
    ref = facts.evidence[0].source_ref
    kind = {"target": "review_target", "key_result": "review_key_result"}.get(resource, resource)
    if resource not in {"manifest", "subject"}:
        deps.append(dict(source_type=kind, record_id=str(uuid4()), source_refs=[ref]))

    async def fetchval(query, *args):
        if resource == "manifest" and "work_report_source_access" in query:
            return False
        if resource == "subject" and "authorization_subject($1,$2" in query:
            return False
        if resource in {"opportunity", "visit", "task"} and f"authorization_{resource}($1" in query:
            return False
        if resource == "target" and "FROM crm.sales_target" in query:
            return False
        if resource == "key_result" and "FROM crm.review_key_result" in query:
            return False
        return True

    context.connection.fetchval.side_effect = fetchval
    with pytest.raises(WorkReportError) as exc:
        await service.assert_review_access(
            context.connection, context.actor, facts, dependencies=deps, permission="report.export"
        )
    assert exc.value.status == 403


@pytest.mark.asyncio
async def test_normalized_manifest_does_not_need_display_refs(context):
    _, deps = await prepare(context)
    for dep in deps:
        dep.pop("source_refs")
    await service.assert_review_dependencies(context.connection, context.actor, deps, permission="work_report.write")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        [],
        [{"source_type": "task", "record_id": "bad"}],
        [{"source_type": "made_up", "record_id": str(uuid4())}],
        [
            {
                "source_type": "review_subject",
                "record_id": str(uuid4()),
                "scope_type": "person",
                "required_permissions": [],
            }
        ],
    ],
)
async def test_unbound_or_malformed_private_dependencies_fail_closed(context, bad):
    with pytest.raises(WorkReportError):
        await service.assert_review_dependencies(context.connection, context.actor, bad)


@pytest.mark.asyncio
async def test_missing_evidence_binding_and_actor_mismatch_are_rejected(context):
    facts, deps = await prepare(context)
    incomplete = [{**d, "source_refs": []} for d in deps]
    with pytest.raises(WorkReportError):
        await service.assert_review_access(context.connection, context.actor, facts, dependencies=incomplete)
    context.connection.fetchrow.return_value = dict(workspace_id=str(uuid4()), user_id=context.actor.user_id, rls="on")
    context.connection.fetchrow.side_effect = None
    with pytest.raises(WorkReportError):
        await service.assert_review_dependencies(context.connection, context.actor, deps)
