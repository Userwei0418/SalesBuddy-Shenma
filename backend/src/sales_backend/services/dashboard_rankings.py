from datetime import date, timedelta

from sales_backend.repositories.customer_assets import today
from sales_backend.repositories.dashboard_scope import (
    GROUPS,
    dashboard_team_groups,
    resolve_selection,
    supervised_region_codes,
)
from sales_backend.repositories.rankings import department_ranking_groups, ranking, subject_ranking


async def dashboard_rankings(connection, actor, *, year, quarters, personal, member_id=None, team_groups=()):
    selection = await resolve_selection(
        connection, actor, personal=personal, member_id=member_id, team_groups=team_groups
    )
    quarters = sorted(set(quarters))
    months = [month for quarter in quarters for month in range(quarter * 3 - 2, quarter * 3 + 1)]
    as_of = today()
    target = selection.member_id if selection.personal else None
    acv = await subject_ranking(
        connection, "opportunity_acv", date(year, 1, 1), date(year, 12, 31), months=months, member_id=target
    )
    followup = await subject_ranking(
        connection, "followup", as_of - timedelta(days=6), as_of, months=None, member_id=target
    )
    # Existing facts count keeps its original authorization boundary, separate from company rankings.
    active = await ranking(
        connection, "active_opportunities", date(year, 1, 1), date(year, 12, 31), months=months, personal=False
    )
    # Explicit legacy region parameters retain their original response/cohort.
    # New manager selectors use actual departments, including a zero-value roster.
    dynamic = actor.role.value == "manager" and not selection.personal and (
        not selection.team_groups or any(code.startswith("team:") for code in selection.team_groups)
    )
    if dynamic:
        departments = await dashboard_team_groups(connection, actor)
        team_ids = [row["team_id"] for row in departments]
        for metric, payload, start, end, period_months in (
            ("opportunity_acv", acv, date(year, 1, 1), date(year, 12, 31), months),
            ("followup", followup, as_of - timedelta(days=6), as_of, None),
            ("active_opportunities", active, date(year, 1, 1), date(year, 12, 31), months),
        ):
            groups = await department_ranking_groups(
                connection, actor, metric, start, end, months=period_months, team_ids=team_ids
            )
            payload["groups"] = groups
            payload["calculation"] = "dashboard_actual_departments_v1"
            if metric == "active_opportunities":
                payload["region_groups"] = groups
            else:
                payload["rows"] = groups
        codes = [row["code"] for row in departments
                 if selection.team_ids is None or row["team_id"] in selection.team_ids]
    elif selection.personal:
        codes = acv["subject_region_codes"]
    elif actor.role.value == "manager":
        codes = list(selection.team_groups) or [group["code"] for group in GROUPS]
    else:
        codes = await supervised_region_codes(connection, actor)
    return {
        "contract_version": 2,
        "data_source": "database",
        "scope": acv["scope"],
        "as_of": as_of,
        "year": year,
        "quarters": quarters,
        "selection": {
            "personal": selection.personal,
            "member_id": target,
            "team_groups": codes,
            "cohort_role": acv["cohort_role"],
        },
        "opportunity_acv": acv,
        "followup": followup,
        "active_opportunities": active,
        "region": {"rows": acv["groups"], "groups": []},
        "own_user_id": actor.user_id,
        "own_region_codes": codes,
        "definitions": {
            "opportunity_acv": "预计关单日在所选季度的在推商机 ACV，不含 Won / Lost",
            "active_opportunities": "所选季度有已确认跟进的商机去重，含仍有跟进的 Won，排除 Lost",
            "followup": "近7天（含今天）已确认或归档的跟进记录",
        },
    }
