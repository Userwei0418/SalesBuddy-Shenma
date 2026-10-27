"""Knowledge-library contracts, independent of commercial/finance materials.

Provider types describe our ports, not a vendor HTTP payload. Trusted directory,
CRM, file and authorization snapshots must be loaded by the backend per request.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    model_validator,
)


def _non_nil(value: UUID) -> UUID:
    if value.int == 0:
        raise ValueError("标识不能为空 UUID")
    return value


def _plain(value: str) -> str:
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("文本包含无效控制字符")
    value.encode("utf-8")
    return value


KnowledgeId = Annotated[UUID, AfterValidator(_non_nil)]
Code = Annotated[str, Field(strict=True, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")]
Label = Annotated[str, Field(strict=True, min_length=1, max_length=300), AfterValidator(_plain)]
Note = Annotated[str, Field(strict=True, max_length=2000), AfterValidator(_plain)]
Revision = Annotated[int, Field(strict=True, ge=1)]
KnowledgeAction = Literal["read", "edit", "manage"]
ContentCategory = Literal[
    "overall_solution", "product_intro", "industry_solution", "customer_solution",
    "sales_support", "training_assessment", "conference_speech",
]
ContentScope = Literal["internal", "customer", "partner", "public"]
VersionState = Literal["saved", "syncing", "parsing", "retrievable", "failed", "deleted"]


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class KnowledgeEnumOption(KnowledgeModel):
    code: Code
    label: Label
    active: bool = True


class KnowledgeCatalog(KnowledgeModel):
    """Current, company-scoped configuration; no guessed product/industry values."""

    company_id: KnowledgeId
    revision: Revision
    general_product_code: Code
    products: tuple[KnowledgeEnumOption, ...]
    industries: tuple[KnowledgeEnumOption, ...] = ()
    reporting_positions: tuple[KnowledgeEnumOption, ...] = ()
    reporting_levels: tuple[KnowledgeEnumOption, ...] = ()
    decision_roles: tuple[KnowledgeEnumOption, ...] = ()

    @model_validator(mode="after")
    def unique_codes(self):
        for name in ("products", "industries", "reporting_positions", "reporting_levels", "decision_roles"):
            values = getattr(self, name)
            if len(values) != len({item.code for item in values}):
                raise ValueError(f"{name} 存在重复编码")
        if not any(p.code == self.general_product_code and p.active for p in self.products):
            raise ValueError("产品目录必须明确有效的通用产品编码")
        return self


class KnowledgeContent(KnowledgeModel):
    file_ids: tuple[KnowledgeId, ...] = Field(default=(), max_length=100)
    links: tuple[HttpUrl, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def sources(self):
        if not self.file_ids and not self.links:
            raise ValueError("内容材料必须至少有附件或链接")
        if len(set(self.file_ids)) != len(self.file_ids) or len(set(self.links)) != len(self.links):
            raise ValueError("内容材料不能重复")
        if any(url.username is not None or url.password is not None for url in self.links):
            raise ValueError("链接不能包含用户名或密码")
        return self


class KnowledgeContributionInput(KnowledgeModel):
    """Editable contribution fields. Generated name/date/author are forbidden."""

    expected_catalog_revision: Revision
    content_category: ContentCategory
    content_scope: ContentScope
    product_code: Code
    industry_code: Code | None = None
    customer_id: KnowledgeId | None = None
    reporting_position_code: Code | None = None
    reporting_level_code: Code | None = None
    decision_role_code: Code | None = None
    opportunity_id: KnowledgeId | None = None
    supplementary_text: Annotated[str, Field(strict=True, max_length=300), AfterValidator(_plain)] = ""
    content: KnowledgeContent
    remarks: Note = ""

    @model_validator(mode="after")
    def conditional_fields(self):
        industry_required = self.content_category in {"industry_solution", "customer_solution"}
        if industry_required != (self.industry_code is not None):
            raise ValueError("仅行业方案/客户方案必须选择行业；其他类别不得携带隐藏行业")
        customer_fields = (
            self.customer_id, self.reporting_position_code, self.reporting_level_code, self.decision_role_code,
        )
        if self.content_category == "customer_solution":
            if any(value is None for value in customer_fields):
                raise ValueError("客户方案必须选择标准客户及汇报对象岗位、职级、决策角色")
        elif any(value is not None for value in customer_fields):
            raise ValueError("非客户方案不得携带隐藏客户/汇报对象字段")
        return self


class KnowledgeCRMReference(KnowledgeModel):
    """Backend-resolved current reference; never accept this object from clients."""

    company_id: KnowledgeId
    id: KnowledgeId
    kind: Literal["customer", "opportunity"]
    name: Label
    active: bool = True


class KnowledgeContribution(KnowledgeModel):
    company_id: KnowledgeId
    selection: KnowledgeContributionInput
    content_name: Annotated[str, Field(min_length=1, max_length=2000)]
    customer_name: Label | None = None
    opportunity_name: Label | None = None


class KnowledgeMaterial(KnowledgeModel):
    company_id: KnowledgeId
    material_id: KnowledgeId
    active_version_id: KnowledgeId | None = None
    deleted: bool = False


class KnowledgeMaterialVersion(KnowledgeModel):
    company_id: KnowledgeId
    material_id: KnowledgeId
    version_id: KnowledgeId
    version_no: Revision
    contribution: KnowledgeContribution
    uploaded_at: AwareDatetime
    contributor_user_id: KnowledgeId
    state: VersionState = "saved"

    @model_validator(mode="after")
    def contribution_scope(self):
        if self.contribution.company_id != self.company_id:
            raise ValueError("版本内容必须属于同一企业")
        return self


class KnowledgeStoredFile(KnowledgeModel):
    """A successfully archived file. Pending/failed archives cannot use this type."""

    company_id: KnowledgeId
    file_id: KnowledgeId
    storage_provider: Code
    storage_key: Annotated[str, Field(min_length=1, max_length=1000)]
    filename: Label
    media_type: Annotated[str, Field(min_length=1, max_length=200)]
    size_bytes: Annotated[int, Field(strict=True, gt=0)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class KnowledgeVersionRef(KnowledgeModel):
    material_id: KnowledgeId
    version_id: KnowledgeId


class KnowledgeAllowedVersion(KnowledgeVersionRef):
    acl_revision: Revision


class KnowledgeAccessScope(KnowledgeModel):
    purpose: Literal["retrieval", "delivery"] = "retrieval"
    company_id: KnowledgeId
    user_id: KnowledgeId
    authorization_revision: Revision
    versions: tuple[KnowledgeAllowedVersion, ...] = ()
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def distinct_materials(self):
        if len(self.versions) != len({v.material_id for v in self.versions}):
            raise ValueError("允许集合中每个材料只能对应一个固定版本")
        return self


class KnowledgeDocumentBinding(KnowledgeModel):
    company_id: KnowledgeId
    source: KnowledgeVersionRef
    provider_document_id: Annotated[str, Field(min_length=1, max_length=300)]
    provider_dataset_id: Annotated[str, Field(min_length=1, max_length=300)] | None = None


class KnowledgeRetrievalRequest(KnowledgeModel):
    """Internal request: a nonempty exact allowlist, mandatory for every provider."""

    scope: KnowledgeAccessScope
    query: Annotated[str, Field(min_length=1, max_length=8000)]
    documents: tuple[KnowledgeDocumentBinding, ...]

    @model_validator(mode="after")
    def exact_documents(self):
        if self.scope.purpose != "retrieval":
            raise ValueError("历史产物交付快照不能用于新检索")
        allowed = {(v.material_id, v.version_id) for v in self.scope.versions}
        bound = {(d.source.material_id, d.source.version_id) for d in self.documents}
        if not allowed:
            raise ValueError("空允许集合禁止调用供应商检索")
        if bound != allowed:
            raise ValueError("供应商映射必须完整且精确对应允许集合")
        if any(d.company_id != self.scope.company_id for d in self.documents):
            raise ValueError("供应商映射跨企业")
        if len({d.provider_document_id for d in self.documents}) != len(self.documents):
            raise ValueError("供应商文档映射必须唯一")
        return self


class KnowledgeChunk(KnowledgeModel):
    source: KnowledgeVersionRef
    provider_document_id: Annotated[str, Field(min_length=1, max_length=300)]
    chunk_id: Annotated[str, Field(min_length=1, max_length=300)]
    text: Annotated[str, Field(min_length=1, max_length=100000)]
    page: Annotated[int, Field(strict=True, ge=1)] | None = None


class KnowledgeCitation(KnowledgeModel):
    source: KnowledgeVersionRef
    provider_document_id: Annotated[str, Field(min_length=1, max_length=300)]
    chunk_id: Annotated[str, Field(min_length=1, max_length=300)]


class KnowledgeCount(KnowledgeModel):
    status: Literal["available", "unconfigured", "unavailable", "stale"]
    count: Annotated[int, Field(strict=True, ge=0)] | None
    source: Code | None = None
    observed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def reliable_count(self):
        if self.status == "available":
            if self.count is None or self.source is None or self.observed_at is None:
                raise ValueError("可靠计数必须有数值、来源与观测时点")
        elif self.count is not None:
            raise ValueError("未配置/不可用/过期计数不能冒充当前数值")
        return self


class KnowledgeGenerationRequest(KnowledgeModel):
    scope: KnowledgeAccessScope
    output_format: Literal["docx", "pptx"]
    instructions: Annotated[str, Field(min_length=1, max_length=8000)]
    sources: tuple[KnowledgeChunk, ...]

    @model_validator(mode="after")
    def authorized_sources(self):
        if self.scope.purpose != "retrieval":
            raise ValueError("历史产物交付快照不能用于新生成")
        allowed = {(v.material_id, v.version_id) for v in self.scope.versions}
        if not allowed or not self.sources:
            raise ValueError("生成必须提供授权材料")
        if any((s.source.material_id, s.source.version_id) not in allowed for s in self.sources):
            raise ValueError("生成材料超出允许集合")
        return self


class KnowledgeProviderArtifact(KnowledgeModel):
    """Unarchived vendor output: not a downloadable product success receipt."""

    provider_reference: Annotated[str, Field(min_length=1, max_length=2000)]
    filename: Label
    media_type: Annotated[str, Field(min_length=1, max_length=200)]
    expires_at: AwareDatetime | None = None


class KnowledgeRetrievalPort(Protocol):
    async def retrieve(self, request: KnowledgeRetrievalRequest) -> tuple[KnowledgeChunk, ...]: ...


class KnowledgeGenerationPort(Protocol):
    async def generate(self, request: KnowledgeGenerationRequest) -> tuple[KnowledgeProviderArtifact, ...]: ...


class KnowledgeFileArchivePort(Protocol):
    async def archive(
        self, company_id: UUID, file_id: UUID, artifact: KnowledgeProviderArtifact, *, idempotency_key: UUID,
    ) -> KnowledgeStoredFile: ...


class KnowledgeCountPort(Protocol):
    async def count(self, company_id: UUID, user_id: UUID) -> KnowledgeCount: ...


# The backend supplies verified, currently attachable files; this is not an ACL bypass.
KnowledgeAttachableFiles = Mapping[UUID, KnowledgeStoredFile]
