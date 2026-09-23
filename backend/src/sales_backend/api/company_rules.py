from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity, get_settings
from sales_backend.api.idempotency import MutationKey
from sales_backend.api.management_dependencies import get_management_identity, get_system_identity
from sales_backend.db import Database
from sales_backend.domain.agent_management import management_metadata
from sales_backend.domain.company_rules import (
    TECHNICAL_CAPABILITIES,
    TECHNICAL_RULES,
    FdeCapabilitiesPolicy,
    QuadrantPolicy,
    TaskSchedulePolicy,
    VisitAdmissionPolicy,
    policy_snapshot,
    validate_policy,
)
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.services.operations import management_write

router = APIRouter(prefix="/api/v1", tags=["Company policies"])
repository = CompanyRulesRepository()


class Draft(BaseModel):
    id: UUID | None = None
    revision: int | None = Field(None, ge=1)
    base_id: UUID | None = None
    definition: dict
    reason: str = Field(min_length=1, max_length=500)


class Publish(BaseModel):
    revision: int = Field(ge=1)


class Restore(BaseModel):
    base_id: UUID | None = None
    reason: str = Field(min_length=1, max_length=500)


class CatalogSummary(BaseModel):
    workspace_id: str
    rule_count: int
    capability_count: int


class PolicyCatalog(BaseModel):
    items: list[dict] = Field(
        description=(
            "公司规则或能力卡片；包含管理职责、接口进程已加载的公司绑定配置及中台链接。"
            "management.validation_status 仅表示绑定配置状态，不代表实际调用、执行快照或业务验收结论；"
            "实际调用以运行审计为准。能力卡片附带业务指引草稿与版本。"
        )
    )
    can_publish: bool
    management: CatalogSummary


@router.get("/console/company-rules", response_model=PolicyCatalog)
async def catalog(
    identity: RequestIdentity = Depends(get_management_identity), database: Database = Depends(get_database),
    settings=Depends(get_settings),
):
    from sales_backend.services.agent_platform.inference import binding_for

    bindings = {capability: binding_for(settings.agent_platform_bindings_json,
                                      identity.actor.workspace_id, capability)
                for capability in TECHNICAL_CAPABILITIES}
    async with database.transaction(identity.actor, readonly=True) as connection:
        items = await repository.catalog(connection)
        for item in items:
            item["management"] = management_metadata(item["code"], bindings)
        return {
            "items": items,
            "can_publish": identity.actor.role.value == "administrator",
            "management": {"workspace_id": str(identity.actor.workspace_id),
                           "rule_count": len(items), "capability_count": len(TECHNICAL_CAPABILITIES)},
        }


@router.get("/console/ai/execution", response_model=PolicyCatalog)
async def execution_catalog(
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
    settings=Depends(get_settings),
):
    from sales_backend.repositories.admin import AgentRuntimeConfigRepository
    from sales_backend.services.agent_platform.fde_facts_runtime import filtered_facts_runtime
    from sales_backend.services.agent_platform.inference import binding_for
    from sales_backend.services.runtime_config import apply_execution_policy

    async with database.transaction(identity.actor, readonly=True) as connection:
        items = await repository.catalog(connection, technical=True)
        business = {item["code"]: item for item in await repository.catalog(connection, business=True)}
        provider = await AgentRuntimeConfigRepository().current(connection, identity.actor, include_ciphertext=False)
    original_api_configured = bool(settings.senseaudio_api_key or (
        provider and provider["enabled"] and provider["has_api_key"]
    ))
    for item in items:
        capability = item["code"].split(".", 1)[1]
        effective = apply_execution_policy(settings, item["current"])
        binding = binding_for(effective.agent_platform_bindings_json, identity.actor.workspace_id, capability)
        item["business_rule"] = business[f"agent_business.{capability}"]
        item["management"] = management_metadata(item["code"], {capability: binding})
        adapter = filtered_facts_runtime(None, effective, capability=capability)
        item["runtime"] = {
            "configuration_scope": "api_process",
            "agent_id": binding.agent_id if binding else None,
            "expected_snapshot_id": binding.snapshot_id if binding else None,
            "configuration_ready": bool(binding and adapter and binding.agent_id == adapter.agent_id),
            "original_api_configured": original_api_configured,
            "platform_seconds": effective.agent_inference_platform_seconds,
            "total_seconds": effective.agent_inference_total_seconds,
        }
    return {"items": items, "can_publish": identity.actor.role.value == "administrator",
            "management": {"workspace_id": str(identity.actor.workspace_id),
                           "rule_count": 8, "capability_count": len(items)}}


@router.post("/console/company-rules/{code}/drafts")
async def save_draft(
    code: str,
    body: Draft,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    data = body.model_dump(mode="json")

    async def write(connection):
        if code in TECHNICAL_RULES and identity.actor.role.value != "administrator":
            raise PermissionError("仅系统管理员可编辑 Agent 运行配置")
        data["definition"] = validate_policy(code, body.definition)
        return {"id": await repository.save(connection, code, data)}

    return await management_write(database, identity.actor, idempotency_key, "company_rule.draft", data, write)


@router.post("/console/company-rules/versions/{rule_id}/publish")
async def publish(
    rule_id: UUID,
    body: Publish,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_system_identity),
    database: Database = Depends(get_database),
):
    async def write(connection):
        row = await repository.version(connection, rule_id)
        if not row:
            raise LookupError("规则不存在")
        validate_policy(row["rule_code"], row["definition"])
        return {"id": await repository.publish(connection, str(rule_id), body.revision)}

    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        "company_rule.publish",
        {"id": str(rule_id), **body.model_dump()},
        write,
    )


@router.post("/console/company-rules/versions/{rule_id}/restore")
async def restore(
    rule_id: UUID,
    body: Restore,
    idempotency_key: MutationKey = None,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    async def write(connection):
        row = await repository.version(connection, rule_id)
        if not row or row["status"] not in {"active", "retired"}:
            raise LookupError("已发布版本不存在")
        code = row["rule_code"]
        if code in TECHNICAL_RULES and identity.actor.role.value != "administrator":
            raise PermissionError("仅系统管理员可恢复 Agent 运行配置")
        snapshot = policy_snapshot(code, row)
        data = {
            "definition": snapshot["definition"],
            "reason": body.reason,
            "base_id": str(body.base_id) if body.base_id else None,
        }
        return {"id": await repository.save(connection, code, data, restored=str(rule_id))}

    return await management_write(
        database,
        identity.actor,
        idempotency_key,
        "company_rule.restore",
        {"id": str(rule_id), **body.model_dump(mode="json")},
        write,
    )


@router.post("/console/company-rules/{code}/preview")
async def preview(
    code: str,
    body: Draft,
    identity: RequestIdentity = Depends(get_management_identity),
    database: Database = Depends(get_database),
):
    try:
        definition = validate_policy(code, body.definition)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    async with database.transaction(identity.actor, readonly=True) as connection:
        current = await repository.active(connection, code)
    if code.startswith("agent_business."):
        return {
            "current": current,
            "columns": ["配置项", "当前规则", "草稿规则"],
            "samples": [
                {"label": label, "before": current["definition"].get(key) or "沿用默认业务指引",
                 "after": definition[key] or "沿用默认业务指引"}
                for key, label in [("guidance", "业务判断指引"), ("calibration_examples", "判断示例")]
            ],
            "note": "这里只检查配置内容，不调用模型，不代表业务语义验收。发布后仅影响当前公司后续推理；"
                    "固定返回协议、数据权限和公司结构化规则优先于补充指引。",
            "validation_kind": "configuration_only",
        }
    if code == "fde_capabilities":
        before, after = FdeCapabilitiesPolicy(**current["definition"]), FdeCapabilitiesPolicy(**definition)
        return {"current": current, "columns": ["岗位", "当前自主录入", "草稿自主录入"],
                "samples": [{"label": label, "before": "开启" if before.enabled(role) else "关闭",
                             "after": "开启" if after.enabled(role) else "关闭"}
                            for role, label in [("fde", "FDE"), ("fde_lead", "FDE主管")]],
                "note": "例外账号优先于岗位，岗位优先于默认。只控制自主拜访录入；商业编辑、任务处理及问数权限独立。"
                        "关闭后新的录入与归档会被阻止，已保存草稿和历史记录保留。"}
    if code.startswith("score."):
        from sales_backend.domain.profile_scores import SCORE_MODELS, weighted_score

        keys = [key for key in SCORE_MODELS[code].model_fields if key != "schema_version"]
        samples = []
        for label, values in [
            ("均衡表现", {key: 80 for key in keys}),
            ("各项有差异", {key: 90 - i * 10 for i, key in enumerate(keys)}),
            ("只具备一项数据", {keys[0]: 60}),
        ]:
            before = weighted_score(values, current)
            after = weighted_score(values, {**current, "definition": definition})
            samples.append(
                {
                    "label": label,
                    "before": before["text"] + "分 / 覆盖 " + str(before["coverage_percent"]) + "%",
                    "after": after["text"] + "分 / 覆盖 " + str(after["coverage_percent"]) + "%",
                }
            )
        return {
            "current": current,
            "columns": ["试算场景", "当前权重", "草稿权重"],
            "samples": samples,
            "note": (
                "这是规则试算样例。各项封顶100分，缺项按已有权重归一并显示覆盖率。"
                "发布后用于当前经营总分展示，不覆盖已归档的历史评分。"
            ),
        }
    if code in TECHNICAL_RULES:

        def label(d):
            route = "原 API" if d["strategy"] == "direct_only" else "沿用已批准的中台范围"
            return route + (
                f"；中台{d['platform_seconds']}秒 / 总{d['total_seconds']}秒"
                if d["override_budget"]
                else "；沿用部署预算"
            )

        return {
            "current": current,
            "columns": ["影响范围", "当前配置", "草稿配置"],
            "samples": [
                {"label": "此能力后续新运行", "before": label(current["definition"]), "after": label(definition)}
            ],
            "note": "只影响后续运行；不扩大现有账号范围、不授予数据库工具权限。中台超时或结果不合格仍尝试原 API。",
        }
    if code == "task_schedule":
        before, after = TaskSchedulePolicy(**current["definition"]), TaskSchedulePolicy(**definition)
        samples = []
        for stamp, value in [
            ("2026-09-12T00:00:00+08:00", None),
            ("2026-09-12T11:00:00+08:00", None),
            ("2026-09-12T18:00:00+08:00", "无法解析"),
            ("2026-09-12T18:00:00+08:00", "2026-09-15T09:00:00+08:00"),
        ]:
            now = datetime.fromisoformat(stamp)

            def formatted(policy, value=value, now=now):
                return policy.due(value, now).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%m月%d日 %H:%M")

            label = " / 已有明确日期" if value and value != "无法解析" else " / 缺失或无效日期"
            samples.append(
                {
                    "label": now.strftime("%m月%d日 %H:%M") + label,
                    "before": formatted(before),
                    "after": formatted(after),
                }
            )
        return {
            "current": current,
            "columns": ["触发时间（北京）", "当前执行时间", "草稿执行时间"],
            "samples": samples,
            "note": "只为新生成的本人拜访待办补未来时间，已有任务和管理任务日期不改。"
            "时刻按所选时区解释；既有基线使用UTC日界，09:00/02:00对应北京时间17:00/10:00。",
        }
    if code == "home_display":
        labels = {"desc": "最新在上", "asc": "最新在下"}
        return {
            "current": current,
            "columns": ["应用位置", "当前顺序", "草稿顺序"],
            "samples": [
                {
                    "label": "首页动态",
                    "before": labels[current["definition"]["message_order"]],
                    "after": labels[definition["message_order"]],
                }
            ],
            "note": "重新进入首页时应用；停留期间不改变顺序。个人筛选不变，问数入口继续隐藏。",
        }
    if code == "visit_admission":
        before, after = VisitAdmissionPolicy(**current["definition"]), VisitAdmissionPolicy(**definition)
        return {
            "current": current,
            "columns": ["分数 / 下一步", "当前准入", "草稿准入"],
            "samples": [
                {
                    "label": f"{score}分 / {'通过' if next_ok else '不通过'}",
                    "before": before.grade(score)
                    + (" · 可归档" if before.admits(score) and next_ok else " · 不可归档"),
                    "after": after.grade(score) + (" · 可归档" if after.admits(score) and next_ok else " · 不可归档"),
                }
                for score, next_ok in [(60, True), (61, True), (after.score_threshold, True), (80, False), (90, True)]
            ],
            "note": "固定审核样例，假设必填信息完整；下一步不通过始终不能归档。评分细则需另用业务样例验证。",
        }
    before, after = QuadrantPolicy(**current["definition"]), QuadrantPolicy(**definition)
    samples = [(60, 60), (70, 70), (71, 69), (90, 90), (after.potential_threshold, after.relationship_threshold)]
    return {
        "current": current,
        "samples": [
            {"potential": p, "relationship": r, "before": before.classify(p, r), "after": after.classify(p, r)}
            for p, r in samples
        ],
        "note": "固定分数样例试算，不调用模型、不重算历史客户。评分细则效果需另外用业务样例验证。",
    }


@router.get("/company-rules/presentation")
async def presentation(identity: RequestIdentity = Depends(get_identity), database: Database = Depends(get_database)):
    async with database.transaction(identity.actor, readonly=True) as connection:
        return {
            code: await repository.active(connection, code)
            for code in ("customer_quadrant", "visit_admission", "home_display")
        }
