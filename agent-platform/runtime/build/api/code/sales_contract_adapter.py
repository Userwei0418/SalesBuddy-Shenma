import copy

from sales_contracts.domain.agent import ActorContext

from sales_contracts.integrations.supreme_fde import FdeResult, RunIds

from sales_contracts.services.agent_run.models import RunInput

from sales_contracts.services.agent_platform.result_contracts import validate_run_result

from sales_contracts.domain.advice import validate_advice

from sales_contracts.domain.opportunity_change import validate_assessment

from sales_contracts.domain.competency_review import validate_competency_result

from sales_contracts.services.battle_map_reviews import BattleMapReviewHandler

CONTRACTS={'visit_entry': {}, 'visit_quality': {}, 'opportunity_draft': {}, 'customer_advice': {}, 'opportunity_advice': {}, 'visit_advice': {}, 'opportunity_change': {}, 'operating_report_fde': {}, 'operating_report_fde_lead': {}, 'operating_report_sales': {}, 'operating_report_supervisor': {}, 'operating_report_manager': {}, 'fde_coaching': {}, 'competency_review': {}, 'today_tasks': {}, 'personal_risks': {}, 'chatbi': {}, 'customer_chatbi': {}, 'battle_map_review': {}}

def validate(contract, raw_answer, context):
    """context is the trusted backend input, not fields from model output.

    Never generate context from an Agent's own answer. This function has no
    network/database calls and does not accept/save business records.
    """
    if contract not in CONTRACTS:
        raise ValueError("Unknown contract")
    if context.get("contract") != contract:
        raise ValueError("Trusted context contract mismatch")
    if contract in {"visit_entry", "visit_quality"}:
        stage = "quality" if contract == "visit_quality" else "structure"
        if context["facts"].get("visit_stage") != stage:
            raise ValueError("Trusted visit stage mismatch")
    if contract.startswith("operating_report_"):
        if context["actor"]["role"] != contract.removeprefix("operating_report_"):
            raise ValueError("Trusted report role mismatch")
        if context["run"].get("surface") == "fde_profile":
            raise ValueError("Use the fde_coaching contract for this surface")
    if contract == "fde_coaching" and context["run"].get("surface") != "fde_profile":
        raise ValueError("Trusted coaching surface mismatch")
    result = FdeResult(raw_answer, RunIds(), None, None).json_object()
    facts = copy.deepcopy(context["facts"])
    if contract in {"customer_advice", "opportunity_advice", "visit_advice"}:
        return validate_advice(result, facts)
    if contract == "opportunity_change":
        return validate_assessment(result, facts)
    if contract == "competency_review":
        return validate_competency_result(result, context["framework"], facts)
    if contract == "battle_map_review":
        return BattleMapReviewHandler._normalize_result(result, facts)
    actor = ActorContext.model_validate(context["actor"])
    run_args = context["run"]
    expected_mode = (
        "visit_entry" if contract in {"visit_entry", "visit_quality"}
        else "operating_report" if contract.startswith("operating_report_") or contract == "fde_coaching"
        else contract
    )
    if run_args["mode"] != expected_mode:
        raise ValueError("Trusted run mode mismatch")
    run = RunInput(run_id="fixture-run", conversation_id="fixture-conversation",
                   text=run_args.get("text", "合成校验样例"), mode=run_args["mode"],
                   customer_id=None, actor=actor, surface=run_args.get("surface"))
    return validate_run_result(run, result, facts)

def explain_error(error):
    """Report paths/codes without returning field values from a rejected answer."""
    chain = []
    current = error
    for _ in range(8):
        if current is None:
            break
        entry = {"type": type(current).__name__}
        code = getattr(current, "contract_code", None) or getattr(current, "code", None)
        if code:
            entry["code"] = code
        if hasattr(current, "errors"):
            entry["fields"] = [{"path": list(x["loc"]), "type": x["type"]}
                               for x in current.errors(include_input=False, include_url=False)]
        elif type(current) is ValueError and str(current) in {
            "质检改写了已核对的内容，请重新质检", "下一步计划审核结论与依据不一致",
            "建议重复或引用了未提供的事实", "变化评估必须引用本次提供的事实",
            "battle-map Agent returned invalid scores", "battle-map Agent returned invalid evidence",
            "company scoring policy receipt missing or mismatched",
            "今日待办引用了本次事实以外的对象", "今日待办重复引用同一来源",
            "今日待办优先级不合法", "风险类型或级别不合法", "问数结果格式不完整",
        }:
            # Only literal diagnostics, never a rendered Pydantic input/value.
            entry["message"] = str(current)
        chain.append(entry)
        current = current.__cause__
    return {"accepted": False, "errors": chain}
