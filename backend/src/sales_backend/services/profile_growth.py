"""Complete current group projections; selecting a subject never changes actor."""

from sales_backend.repositories import profile_growth
from sales_backend.repositories.profile import ProfileRepository
from sales_backend.repositories.profile_performance import ProfilePerformanceRepository
from sales_backend.services.growth_projection import CONTRACT_VERSION, group_projection, member_coverage
from sales_backend.services.profile_scores import competency_score


async def scoped_growth(connection, actor, *, scope, account_code=None, team=None,
                        member_id=None, team_id=None, days=30):
    scopes = ProfilePerformanceRepository()
    selected = await scopes.scope(connection, actor, scope=scope, account_code=account_code, team=team,
                                  member_id=member_id, team_id=team_id)
    members = await profile_growth.eligible_members(
        connection, actor.workspace_id, await scopes.members(connection, actor, selected)
    )
    if selected.get("subject_role") == "manager":
        members = []
    provenance = {"scope": selected, "window_days": days}
    if selected["scope"] == "person":
        if not members:
            return {"contract_version": CONTRACT_VERSION, "assessment_kind": "sales_competency",
                    "data_source": "database", "latest": None, "history": [], "framework": None,
                    "score_policy": None, "current_review": None, "can_review": False,
                    "projection": "person", "today_status": "not_applicable", "applicable": False,
                    "reason": "当前成员岗位不适用销售六维画像", "coverage": member_coverage([], 0),
                    "provenance": provenance}
        result = await ProfileRepository().competency_growth(connection, actor, days=days, subject_user_id=members[0])
        result["coverage"] = member_coverage([result["latest"]] if result["latest"] else [], 1)
        result["projection"] = "person"
    else:
        rows = await profile_growth.latest_reviews(connection, actor.workspace_id, members, days)
        result = group_projection(rows, len(members))
        provenance["source_reviews"] = [
            {"id": r["id"], "subject_user_id": r["subject_user_ref_id"], "reviewed_at": r["reviewed_at"],
             "generation_no": r["generation_no"]} for r in rows
        ]
    result["data_source"] = "database"
    result["provenance"] = provenance
    return await competency_score(connection, result)
