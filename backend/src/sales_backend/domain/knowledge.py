"""Pure knowledge contribution and version rules; no I/O or finance concepts."""

from collections.abc import Iterable, Mapping
from datetime import datetime
from uuid import UUID

from sales_backend.contracts.knowledge import (
    ContentCategory,
    ContentScope,
    KnowledgeAttachableFiles,
    KnowledgeCatalog,
    KnowledgeChunk,
    KnowledgeContribution,
    KnowledgeContributionInput,
    KnowledgeCRMReference,
    KnowledgeEnumOption,
    KnowledgeGenerationRequest,
    KnowledgeMaterial,
    KnowledgeMaterialVersion,
    KnowledgeRetrievalRequest,
    VersionState,
)

CATEGORY_LABELS: Mapping[ContentCategory, str] = {
    "overall_solution": "总方案", "product_intro": "产品介绍", "industry_solution": "行业方案",
    "customer_solution": "客户方案", "sales_support": "销售支持", "training_assessment": "培训考核",
    "conference_speech": "会议演讲",
}
SCOPE_LABELS: Mapping[ContentScope, str] = {
    "internal": "仅内部", "customer": "对客", "partner": "对伙伴", "public": "完全公开",
}


class KnowledgeRuleError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def _option(options: tuple[KnowledgeEnumOption, ...], code: str) -> str:
    for option in options:
        if option.code == code and option.active:
            return option.label
    raise KnowledgeRuleError("KNOWLEDGE_ENUM_INVALID", "请选择当前有效的配置枚举")


def _crm(reference: KnowledgeCRMReference | None, id_: UUID | None, kind: str, company_id: UUID) -> str | None:
    if id_ is None:
        if reference is not None:
            raise KnowledgeRuleError("KNOWLEDGE_REFERENCE_INVALID", "未选择的 CRM 引用不能附带解析值")
        return None
    if (reference is None or reference.id != id_ or reference.kind != kind
            or reference.company_id != company_id or not reference.active):
        raise KnowledgeRuleError("KNOWLEDGE_REFERENCE_INVALID", "CRM 引用尚未通过当前企业目录核对")
    return reference.name


def resolve_contribution(
    selection: KnowledgeContributionInput, catalog: KnowledgeCatalog, *, company_id: UUID,
    customer: KnowledgeCRMReference | None = None, opportunity: KnowledgeCRMReference | None = None,
    attachable_files: KnowledgeAttachableFiles,
) -> KnowledgeContribution:
    """Also used for name preview. Save must repeat with fresh trusted snapshots."""
    if catalog.company_id != company_id:
        raise KnowledgeRuleError("KNOWLEDGE_CATALOG_SCOPE", "枚举目录不属于当前企业")
    if selection.expected_catalog_revision != catalog.revision:
        raise KnowledgeRuleError("KNOWLEDGE_CATALOG_CHANGED", "枚举已更新，请重新选择并预览")
    product = _option(catalog.products, selection.product_code)
    industry = _option(catalog.industries, selection.industry_code) if selection.industry_code else None
    for name, code in (
        ("reporting_positions", selection.reporting_position_code),
        ("reporting_levels", selection.reporting_level_code), ("decision_roles", selection.decision_role_code),
    ):
        if code is not None:
            _option(getattr(catalog, name), code)
    customer_name = _crm(customer, selection.customer_id, "customer", company_id)
    opportunity_name = _crm(opportunity, selection.opportunity_id, "opportunity", company_id)
    for file_id in selection.content.file_ids:
        file = attachable_files.get(file_id)
        if file is None or file.file_id != file_id or file.company_id != company_id:
            raise KnowledgeRuleError("KNOWLEDGE_FILE_DENIED", "附件尚未归档或未获当前关联权限")
    parts = [
        product if selection.product_code != catalog.general_product_code else None,
        CATEGORY_LABELS[selection.content_category], SCOPE_LABELS[selection.content_scope],
        industry, customer_name, selection.supplementary_text,
    ]
    return KnowledgeContribution(
        company_id=company_id, selection=selection, content_name="｜".join(p for p in parts if p),
        customer_name=customer_name, opportunity_name=opportunity_name,
    )


def require_distinct_name(
    name: str, names_by_material: Mapping[UUID, str], *, updating_material_id: UUID | None = None,
) -> None:
    """Names must be from the same company and the caller's authorized catalog.

    The repository must serialize duplicate checks with the eventual write.
    A name collision never silently selects a material to update.
    """
    if any(existing == name for id_, existing in names_by_material.items() if id_ != updating_material_id):
        raise KnowledgeRuleError("KNOWLEDGE_NAME_CONFLICT", "同名材料已存在，请明确更新原材料或填写人工补充")


def create_version(
    material: KnowledgeMaterial, contribution: KnowledgeContribution, *, version_id: UUID, version_no: int,
    contributor_user_id: UUID, uploaded_at: datetime,
) -> KnowledgeMaterialVersion:
    if material.deleted:
        raise KnowledgeRuleError("KNOWLEDGE_MATERIAL_DELETED", "已删除材料不能新增版本")
    if contribution.company_id != material.company_id:
        raise KnowledgeRuleError("KNOWLEDGE_VERSION_SCOPE", "材料内容不属于当前企业")
    if version_id == material.active_version_id:
        raise KnowledgeRuleError("KNOWLEDGE_VERSION_REUSED", "新版本不能复用当前版本 ID")
    return KnowledgeMaterialVersion(
        company_id=material.company_id, material_id=material.material_id, version_id=version_id,
        version_no=version_no, contribution=contribution, contributor_user_id=contributor_user_id,
        uploaded_at=uploaded_at,
    )


_TRANSITIONS: Mapping[VersionState, frozenset[VersionState]] = {
    "saved": frozenset({"syncing", "deleted"}),
    "syncing": frozenset({"parsing", "failed", "deleted"}),
    "parsing": frozenset({"retrievable", "failed", "deleted"}),
    "retrievable": frozenset({"deleted"}),
    "failed": frozenset({"syncing", "deleted"}),
    "deleted": frozenset(),
}


def transition_version(version: KnowledgeMaterialVersion, target: VersionState) -> KnowledgeMaterialVersion:
    if target not in _TRANSITIONS[version.state]:
        raise KnowledgeRuleError("KNOWLEDGE_STATE_CONFLICT", "材料版本状态不能执行此转换")
    return version.model_copy(update={"state": target})


def activate_version(material: KnowledgeMaterial, version: KnowledgeMaterialVersion) -> KnowledgeMaterial:
    if material.deleted or version.company_id != material.company_id or version.material_id != material.material_id:
        raise KnowledgeRuleError("KNOWLEDGE_VERSION_SCOPE", "版本不属于当前有效材料")
    if version.state != "retrievable":
        raise KnowledgeRuleError("KNOWLEDGE_NOT_RETRIEVABLE", "新版本验证可检索前不能激活")
    return material.model_copy(update={"active_version_id": version.version_id})


def require_output_format(output_format: str, verified_formats: Iterable[str]) -> None:
    """Capabilities come from separately accepted service configuration, never a default success."""
    if output_format not in {"docx", "pptx"} or output_format not in verified_formats:
        raise KnowledgeRuleError("KNOWLEDGE_FORMAT_UNAVAILABLE", "该文件格式尚未完成接入验收")


def build_generation_request(
    retrieval: KnowledgeRetrievalRequest, chunks: tuple[KnowledgeChunk, ...], *, output_format: str,
    verified_formats: Iterable[str], instructions: str,
) -> KnowledgeGenerationRequest:
    from sales_backend.domain.knowledge_access import validate_chunks

    require_output_format(output_format, verified_formats)
    return KnowledgeGenerationRequest(
        scope=retrieval.scope, output_format=output_format, instructions=instructions,
        sources=validate_chunks(retrieval, chunks),
    )
