"""Group competency views are current projections, never fabricated review histories."""

from sales_backend.domain.profile_scores import number
from sales_backend.repositories import profile_growth
from sales_backend.repositories.profile import ProfileRepository
from sales_backend.repositories.profile_performance import ProfilePerformanceRepository
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
    if selected["scope"] == "person" and not members:
        return {"data_source": "database", "latest": None, "history": [], "framework": None,
                "projection": "person", "today_status": "not_applicable",
                "applicable": False, "reason": "当前成员岗位不适用销售六维画像",
                "coverage": {"reviewed_members": 0, "eligible_members": 0}, "provenance": provenance}
    if selected["scope"] == "person" and members:
        result = await ProfileRepository().competency_growth(connection, actor, days=days, subject_user_id=members[0])
        result["coverage"] = {"reviewed_members": int(result["latest"] is not None), "eligible_members": 1}
        result["projection"] = "person"
    else:
        definition = await profile_growth.framework(connection, actor.workspace_id)
        reviews = await profile_growth.latest_reviews(connection, actor.workspace_id, members, days)
        dimensions = {}
        for dimension in (definition or {}).get("dimensions", []):
            code = dimension["code"]
            values = [number((r["dimension_scores"].get(code) or {}).get("score")) for r in reviews]
            valid = [value for value in values if value is not None]
            dimensions[code] = {
                "score": sum(valid) / len(valid) if valid else None,
                "sample_count": len(valid),
                "assessment": "已完成复盘成员的最新评分均值",
                "coaching_action": "切换到个人查看具体证据和训练建议",
            }
        result = {
            "framework": definition,
            "latest": None,
            "history": [],
            "projection": "current_group",
            "today_status": "succeeded" if reviews else "missing",
            "coverage": {"reviewed_members": len(reviews), "eligible_members": len(members)},
        }
        if reviews:
            result["latest"] = {
                "dimension_scores": dimensions,
                "review_date": max(r["review_date"] for r in reviews),
                "summary": (
                    f"{len(members)} 位适用成员中，{len(reviews)} 位在近 {days} 天有已完成复盘；"
                    "按每人最新有效评分汇总。"
                ),
                "input_snapshot": {
                    "visit_count": sum((r["input_snapshot"] or {}).get("visit_count", 0) for r in reviews)
                },
                "improvements": [],
            }
        provenance["source_reviews"] = [{"id": r["id"], "reviewed_at": r["reviewed_at"]} for r in reviews]
    result["data_source"] = "database"
    result["provenance"] = provenance
    return await competency_score(connection, result)
