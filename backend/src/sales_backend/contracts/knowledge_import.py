"""Explicit, bounded legacy directory imports; never an archived-file claim."""

import hashlib
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from sales_backend.contracts.knowledge import (
    Code,
    ContentCategory,
    ContentScope,
    KnowledgeId,
    KnowledgeModel,
    Label,
    Note,
    Revision,
)
from sales_backend.domain.knowledge_access import KnowledgeACLGrant

SourceId = Annotated[str, Field(strict=True, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]
SOURCE_LINK = re.compile(r"https?://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?(?:[/?#][^\s]*)?")


class ImportedAttachment(KnowledgeModel):
    name: Label
    source_token: SourceId
    size_bytes: Annotated[int, Field(strict=True, ge=0)] | None = None
    media_type: Annotated[str, Field(max_length=200)] | None = None


class ImportRecord(KnowledgeModel):
    record_id: SourceId
    original_name: Annotated[str, Field(strict=True, min_length=1, max_length=2000)]
    product_label: Label
    category_label: Label
    scope_label: Label
    source_status: Literal["可用", "待复核", "已停用"]
    provider_name: Annotated[str, Field(strict=True, max_length=2000)] = ""
    original_content_text: Annotated[str, Field(strict=True, max_length=12000)] = ""
    links: Annotated[tuple[str, ...], Field(max_length=100)] = ()
    attachments: Annotated[tuple[ImportedAttachment, ...], Field(max_length=100)] = ()
    remarks: Note = ""

    @model_validator(mode="after")
    def references(self):
        for link in self.links:
            parts = urlsplit(link)
            if (
                len(link) > 4000
                or SOURCE_LINK.fullmatch(link) is None
                or parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username is not None
                or parts.password is not None
                or (parts.port is not None and not 1 <= parts.port <= 65535)
                or any(ord(c) < 33 for c in link)
            ):
                raise ValueError("KNOWLEDGE_IMPORT_LINK_INVALID")
        if len(set(self.links)) != len(self.links):
            raise ValueError("KNOWLEDGE_IMPORT_DUPLICATE_LINK")
        if len({a.source_token for a in self.attachments}) != len(self.attachments):
            raise ValueError("KNOWLEDGE_IMPORT_DUPLICATE_ATTACHMENT")
        return self


class KnowledgeImportSource(ImportRecord):
    provider: Literal["feishu_base"] = "feishu_base"
    base_id: SourceId
    table_id: Annotated[str, Field(pattern=r"^tbl[A-Za-z0-9]+$")]
    source_revision: Annotated[int, Field(strict=True, ge=0)]
    validation_issues: tuple[str, ...]


class ImportedSelection(KnowledgeModel):
    """A legacy snapshot, deliberately not KnowledgeContributionInput."""

    product_code: Code
    content_category: ContentCategory
    content_scope: ContentScope


class KnowledgeImportedContribution(KnowledgeModel):
    kind: Literal["legacy_import"]
    company_id: KnowledgeId
    content_name: Annotated[str, Field(min_length=1, max_length=2000)]
    selection: ImportedSelection
    source: KnowledgeImportSource


class KnowledgeImportInput(KnowledgeModel):
    workspace_id: KnowledgeId
    owner_user_id: KnowledgeId
    grants: Annotated[tuple[KnowledgeACLGrant, ...], Field(max_length=200)]
    base_id: SourceId
    table_id: Annotated[str, Field(pattern=r"^tbl[A-Za-z0-9]+$")]
    source_revision: Annotated[int, Field(strict=True, ge=0)]
    records: Annotated[tuple[ImportRecord, ...], Field(min_length=1, max_length=500)]
    dry_run: bool = True

    @model_validator(mode="after")
    def distinct(self):
        if len({r.record_id for r in self.records}) != len(self.records):
            raise ValueError("KNOWLEDGE_IMPORT_DUPLICATE_RECORD")
        if len({(g.subject_kind, g.subject_id) for g in self.grants}) != len(self.grants):
            raise ValueError("KNOWLEDGE_IMPORT_DUPLICATE_GRANT")
        return self


class ImportResultItem(KnowledgeModel):
    record_id: SourceId
    outcome: Literal["created", "updated", "unchanged", "conflict"]
    material_id: KnowledgeId | None
    material_revision: Revision | None
    version_id: KnowledgeId | None
    version_no: Revision | None
    validation_issues: tuple[str, ...]
    conflict_code: str | None = None


class KnowledgeImportResult(KnowledgeModel):
    workspace_id: KnowledgeId
    base_id: SourceId
    table_id: SourceId
    source_revision: int
    dry_run: bool
    items: tuple[ImportResultItem, ...]
    counts: dict[str, int]


CATEGORY_CODES = dict(
    zip(
        ("总方案", "产品介绍", "行业方案", "客户方案", "销售支持", "培训考核", "会议演讲"),
        (
            "overall_solution",
            "product_intro",
            "industry_solution",
            "customer_solution",
            "sales_support",
            "training_assessment",
            "conference_speech",
        ),
        strict=True,
    )
)
SCOPE_CODES = {"仅内部": "internal", "可对客": "customer", "可公开": "public", "可对伙伴": "partner"}


def imported_contribution(body: KnowledgeImportInput, record: ImportRecord) -> KnowledgeImportedContribution:
    """Exact label mapping only. No model guessing or invented CRM metadata."""
    if record.category_label not in CATEGORY_CODES or record.scope_label not in SCOPE_CODES:
        raise ValueError("KNOWLEDGE_IMPORT_UNMAPPED_ENUM")
    issues = []
    if record.source_status != "可用":
        issues.append("SOURCE_REVIEW_REQUIRED" if record.source_status == "待复核" else "SOURCE_DISABLED")
    if not record.links and not record.attachments:
        issues.append("MATERIAL_REFERENCE_MISSING")
    if record.category_label in {"行业方案", "客户方案"}:
        issues.append("INDUSTRY_NOT_MAPPED")
    if record.category_label == "客户方案":
        issues.extend(("CUSTOMER_NOT_MAPPED", "REPORTING_ROLES_NOT_MAPPED"))
    product_code = "legacy_" + hashlib.sha256(record.product_label.encode()).hexdigest()[:24]
    return KnowledgeImportedContribution(
        kind="legacy_import",
        company_id=body.workspace_id,
        content_name=record.original_name,
        selection=ImportedSelection(
            product_code=product_code,
            content_category=CATEGORY_CODES[record.category_label],
            content_scope=SCOPE_CODES[record.scope_label],
        ),
        source=KnowledgeImportSource(
            **record.model_dump(),
            base_id=body.base_id,
            table_id=body.table_id,
            source_revision=body.source_revision,
            validation_issues=tuple(issues),
        ),
    )
