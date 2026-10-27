"""Unused detail aggregates must not change the whole-result query contract."""

from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest

from sales_backend.contracts.opportunity_workspace import OpportunityWorkspaceQuery
from sales_backend.repositories.opportunity_workspace_query import compile_query

ACTOR = SimpleNamespace(user_id=UUID("00000000-0000-4000-8000-000000000001"))
PERIOD = {"year": 2026, "quarter": 4}
SOURCES = {
    "demo_count": "crm.opportunity_demo_scenes d",
    "fde_member_ids": "FROM crm.opportunity_participant p JOIN platform.user_ref u",
    "participants": "FROM crm.opportunity_project_participant p JOIN platform.user_ref u",
    "forecast_recognized": "LEFT JOIN crm.opportunity_forecast forecast",
    "forecast_collection": "LEFT JOIN crm.opportunity_forecast forecast",
    "collection_confidence": "LEFT JOIN crm.opportunity_forecast forecast",
}


def query_sql(config=None, *, limit=20, offset=0):
    request = OpportunityWorkspaceQuery.model_validate({
        "config": config or {}, "limit": limit, "offset": offset,
    })
    return compile_query(request, ACTOR, date_map={}, period=PERIOD, today=date(2026, 10, 17))


def response_values(sql):
    return sql.split("'values',jsonb_build_object(", 1)[1].split("),'finance_meta'", 1)[0]


def test_default_view_skips_unused_sources_and_preserves_typed_internal_columns():
    sql, args = query_sql()
    for source in SOURCES.values():
        assert source not in sql
    for projection in (
        "0::integer AS demo_count",
        "ARRAY[]::uuid[] AS fde_member_ids", "'[]'::jsonb AS fde_member_ids_value",
        "ARRAY[]::uuid[] AS participants", "'[]'::jsonb AS participants_value",
        "NULL::numeric AS forecast_recognized", "NULL::numeric AS forecast_collection",
        "NULL::text AS collection_confidence",
    ):
        assert projection in sql
    for key in SOURCES:
        assert f"'{key}'," not in response_values(sql)
    assert args == [2026, 4, {}, 20, 0]


@pytest.mark.parametrize("field", SOURCES)
def test_column_dependency_keeps_aggregate_and_typed_response_value(field):
    sql, args = query_sql({"columns": [{"key": "name"}, {"key": field}]})
    assert SOURCES[field] in sql
    value_key = field + "_value" if field in {"fde_member_ids", "participants"} else field
    expected = f"'{field}',p.\"{value_key}\""
    if field in {"forecast_recognized", "forecast_collection"}:
        expected += "::text"
    assert expected in response_values(sql)
    assert args == [2026, 4, {}, 20, 0]


@pytest.mark.parametrize("field,operator,value", [
    ("demo_count", "gte", 1),
    ("fde_member_ids", "has_any", [str(ACTOR.user_id)]),
    ("participants", "has_all", [str(ACTOR.user_id)]),
    ("forecast_recognized", "gte", "0.01"),
    ("forecast_collection", "is_empty", None),
    ("collection_confidence", "eq", "high"),
])
def test_hidden_filter_dependency_is_computed_before_full_set_filter_and_pagination(field, operator, value):
    condition = {"field": field, "operator": operator}
    if operator != "is_empty":
        condition["value"] = value
    sql, _ = query_sql({"columns": [{"key": "name"}], "filters": [condition]}, limit=1, offset=1)
    assert sql.index(SOURCES[field]) < sql.index("filtered AS MATERIALIZED") < sql.index("LIMIT ")
    assert f'f."{field}"' in sql.split("filtered AS MATERIALIZED", 1)[1].split("page AS", 1)[0]
    # The existing contract also returns facts used by hidden filters.
    assert f"'{field}'," in response_values(sql)


@pytest.mark.parametrize("field", [
    "demo_count", "forecast_recognized", "forecast_collection", "collection_confidence",
])
def test_hidden_sort_dependency_precedes_limit(field):
    sql, _ = query_sql({
        "columns": [{"key": "name"}], "sorts": [{"field": field, "direction": "desc"}],
    })
    assert sql.index(SOURCES[field]) < sql.index("page AS")
    page = sql.split("page AS", 1)[1].split("SELECT (SELECT", 1)[0]
    assert f'f."{field}"' in page and "f.id ASC" in page
    assert page.index(f'f."{field}"') < page.index("LIMIT ")


def test_hidden_group_dependency_uses_full_filtered_set_and_preserves_money_totals():
    sql, args = query_sql({"columns": [{"key": "name"}], "group_by": "collection_confidence"}, limit=1, offset=2)
    assert SOURCES["collection_confidence"] in sql
    group = sql.split("grouped AS", 1)[1].split("page AS", 1)[0]
    assert 'f."collection_confidence" AS group_value' in group
    assert "FROM filtered f GROUP BY" in group
    assert "count(*) AS count" in group
    assert "count(*) FILTER(WHERE amount IS NULL)" in group
    assert "COALESCE(sum(amount),0)::text AS known_amount_subtotal" in group
    assert "'group_key',p.\"collection_confidence\"" in sql
    assert args[-2:] == [1, 2]


def test_optional_sources_remain_independent_and_forecast_uses_bound_period():
    for field, expected in SOURCES.items():
        sql, args = query_sql({"columns": [{"key": field}]})
        for source in set(SOURCES.values()):
            assert (source in sql) == (source == expected)
        assert args[:2] == [2026, 4]
        if "forecast" in expected:
            assert "forecast.year=$1::integer AND forecast.quarter=$2::integer" in sql


def test_finance_product_lines_capabilities_and_authorization_are_not_optional():
    sql, _ = query_sql({"columns": [{"key": "name"}], "scope": {"kind": "self"}})
    for invariant in (
        "o.workspace_id=common.current_workspace_id()", "o.deleted_at IS NULL",
        "security.authorization_opportunity('opportunity.read',o.id)",
        "FROM crm.opportunity_participant sp", "security.fde_user_is_active(sp.user_ref_id)",
        "security.authorization_opportunity('opportunity.update',o.id)",
        "security.can_manage_fde_members(o.id)",
        "security.authorization_opportunity('actual.read',o.id)",
        "security.authorization_customer('actual.read',o.customer_id)",
        "security.authorization_opportunity('actual.create',o.id)",
        "security.authorization_customer('actual.create',o.customer_id)",
        "FROM crm.effective_actual a JOIN authorized ao", "AND ao.can_actual",
        "'finance_meta',p.finance_meta", "'_can_edit',p.can_edit", "'_can_fde',p.can_fde",
        "'_can_actual',p.can_actual", "'_can_actual_write',p.can_actual_write",
        "'product_lines',p.product_lines", "'product_line_source',p.product_line_source",
        "'intent_product_line_ids',p.intent_product_line_ids", "'legacy_product_line',p.legacy_product_line",
        "FROM filtered) summary", "ORDER BY p.row_position",
    ):
        assert invariant in sql
    assert SOURCES["fde_member_ids"] not in sql  # Display pruning does not remove the self-scope FDE predicate.


def test_full_projection_retains_participant_validity_and_json_contracts():
    sql, _ = query_sql({"columns": [{"key": field} for field in SOURCES]})
    for source in SOURCES.values():
        assert source in sql
    assert "clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to" in sql
    assert "'id',p.user_ref_id,'name',u.display_name,'is_active',security.fde_user_is_active(u.id)" in sql
    assert "'user_id',p.user_ref_id,'role',p.participant_role,'name',u.display_name" in sql
    assert "u.status='active' AND u.deleted_at IS NULL" in sql


def test_query_search_and_actual_values_keep_existing_non_optional_projections():
    sql, args = query_sql({"q": "only-bound-search-value", "columns": [{"key": "actual_recognized"}]})
    assert "FROM crm.effective_actual a JOIN authorized ao" in sql
    assert "CASE WHEN ar.complete THEN ar.subtotal END AS actual_recognized" in sql
    assert "'actual_recognized',p.\"actual_recognized\"::text" in response_values(sql)
    assert 'strpos(lower(COALESCE(f."product_line",' in sql
    assert "only-bound-search-value" in args and "only-bound-search-value" not in sql
