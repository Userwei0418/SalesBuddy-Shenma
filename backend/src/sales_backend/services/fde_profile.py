"""Portrait metrics are database facts; an Agent supplies read-only coaching."""

from sales_backend.domain.agent import AgentMode
from sales_backend.domain.capabilities import FDE_ROLES
from sales_backend.domain.fde_coaching import model_facts
from sales_backend.domain.fde_profile import CONTRACT_VERSION, DIMENSIONS, TZ, factual_summary
from sales_backend.repositories import fde_profile
from sales_backend.repositories.assistant import AssistantRepository
from sales_backend.repositories.capabilities import CapabilityRepository
from sales_backend.repositories.collaboration import scope_members
from sales_backend.services.agent_access import require_agent_access


def require_fde(actor):
    if actor.role.value not in FDE_ROLES:
        raise PermissionError("此画像仅供FDE查看本人协作情况")


def presentation(facts, run=None):
    populated = bool(facts["coaching_inputs"]["sources"])
    status = run["status"] if run else ("missing" if populated else "empty")
    if status not in {"empty", "missing", "queued", "running", "succeeded", "failed"}:
        status = "failed"
    result = (run.get("result") or {}) if run and status == "succeeded" else {}
    advice = [
        {"title": item.get("title", ""), "content": item.get("detail", "")} for item in result.get("action_plan", [])
    ]
    return {
        "data_source": "database",
        "contract_version": CONTRACT_VERSION,
        "as_of": facts["data_as_of"],
        "period": facts["period"],
        "sample_count": facts["sample_count"],
        "framework": {
            "dimensions": [{"code": code, "name": name, "short_name": short} for code, name, short in DIMENSIONS]
        },
        "latest": {
            "dimensions": facts["dimensions"],
            "overall_score": None,
            "summary": factual_summary(facts),
            "advice": advice,
            "reviewed_at": run["completed_at"] if run and status == "succeeded" else None,
        },
        "review_status": status,
        "facts_fingerprint": facts["facts_fingerprint"],
        "review_run_id": run["id"] if run else None,
        "evidence_coverage": facts["evidence_coverage"],
        "scope_note": facts["scope_note"],
    }


async def get_profile(connection, actor, days, *, scope="self", member_id=None, team_id=None):
    require_fde(actor)
    # A leader's selected member is authorized through the same scope resolver
    # used by map/projects. Selection never changes the request's actor identity.
    effective_scope = "team" if member_id and str(member_id) != actor.user_id else scope
    _, ids, members = await scope_members(connection, actor, effective_scope, member_id, team_id=team_id)
    own = len(ids) == 1 and ids[0] == actor.user_id and scope != "team"
    facts = await fde_profile.profile_facts(
        connection, actor, days, **({} if own else {"member_ids": ids, "team_id": team_id}))
    result = presentation(facts, await fde_profile.matching_run(connection, actor, facts) if own else None)
    selected_id = ids[0] if len(ids) == 1 and (scope != "team" or member_id) else None
    selected_name = next((p["name"] for p in members if p["id"] == selected_id), None)
    result.update(scope=scope, member_id=selected_id, member_name=selected_name, can_review=own, history=[])
    if selected_id and (own or any(p["id"] == selected_id for p in members)):
        history = await fde_profile.profile_history(connection, selected_id, days)
        result["history"] = [
            {"date": r["reviewed_at"].astimezone(TZ), "dimensions": r["dimensions"], "overall_score": None}
            for r in reversed(history)
        ]
        if not own and history:
            latest = history[0]
            result["latest"]["advice"] = [
                {"title": a.get("title", ""), "content": a.get("detail", "")} for a in latest["advice"]
            ]
            result["latest"]["reviewed_at"] = latest["reviewed_at"]
            result["review_run_id"] = str(latest["run_id"])
            result["review_status"] = "succeeded"
    if not own:
        subject = selected_name or "所选团队"
        result["latest"]["summary"] = result["latest"]["summary"].replace("本人", subject)
        result["scope_note"] = result["scope_note"].replace("本人", "所选成员")
        for dim in result["latest"]["dimensions"]:
            dim["assessment"] = dim["assessment"].replace("本人", "所选成员")
        result["scope_note"] += "负责人只读查看；已有AI建议标注原生成时间，团队无历史快照时不补造趋势。"
    return result


async def request_review(connection, actor, days):
    require_fde(actor)
    await require_agent_access(connection, actor, "operating_report")
    await fde_profile.lock_profile(connection, actor, days)
    facts = await fde_profile.profile_facts(connection, actor, days)
    existing = await fde_profile.matching_run(connection, actor, facts, reusable=True)
    if existing:
        return presentation(facts, existing)
    if presentation(facts)["review_status"] == "empty":
        return presentation(facts)
    repo = AssistantRepository()
    conversation = await repo.create_conversation(connection, actor, mode=AgentMode.OPERATING_REPORT, customer_id=None)
    # surface never comes from conversation context or a client request. It is
    # attached only to this server-created run within the enqueue transaction.
    identity = await CapabilityRepository().analysis_identity(connection, actor)
    run_id = await repo.enqueue_message(
        connection,
        actor,
        conversation_id=conversation["id"],
        text=f"结合近{days}天本人沟通、下一步计划与当前未完任务，判断是否有值得补充的行动。范围仅本人。",
        client_message_id=None,
        input_source="text",
        identity_snapshot=identity,
    )
    await fde_profile.bind_profile_run(connection, run_id, facts)
    return presentation(facts, {"id": run_id, "status": "queued", "completed_at": None})


async def current_run_facts(connection, run):
    require_fde(run.actor)
    if run.mode != "operating_report" or run.surface != "fde_profile" or not run.facts_fingerprint:
        raise PermissionError("FDE画像运行来源不合法")
    facts = await fde_profile.profile_facts(connection, run.actor, run.profile_days)
    if facts["permission_version"] != run.permission_version:
        raise PermissionError("FDE权限已变化，请刷新画像")
    if facts["facts_fingerprint"] != run.facts_fingerprint:
        raise PermissionError("FDE画像事实已变化，请重新生成建议")
    return model_facts(facts)
