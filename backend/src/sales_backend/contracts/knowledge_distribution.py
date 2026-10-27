"""Customer distribution is explicitly published independently of internal ACL."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from sales_backend.contracts.knowledge import ContentCategory, KnowledgeModel

Token = Annotated[str, Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
BrowserId = Annotated[str, Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]
FieldName = Literal["name", "company", "contact", "contact_intent"]


class QuestionnaireField(KnowledgeModel):
    name: FieldName
    required: bool = False
    enabled: bool = True

    @model_validator(mode="after")
    def consistent(self):
        if self.required and not self.enabled:
            raise ValueError("required field must be enabled")
        return self


class Questionnaire(KnowledgeModel):
    enabled: bool = True
    fields: tuple[QuestionnaireField, ...] = (
        QuestionnaireField(name="name", required=True),
        QuestionnaireField(name="company", required=True),
        QuestionnaireField(name="contact", required=True),
        QuestionnaireField(name="contact_intent"),
    )

    @model_validator(mode="after")
    def complete(self):
        if {f.name for f in self.fields} != {"name", "company", "contact", "contact_intent"} or len(self.fields) != 4:
            raise ValueError("exactly four unique questionnaire fields required")
        return self


class PublicationWrite(KnowledgeModel):
    expected_revision: int = Field(ge=0, strict=True)
    version_id: UUID
    enabled: bool = True
    notify: bool = Field(
        default=True, strict=True,
        description="Send the normal publication notification. False still synchronizes the Feishu directory silently.",
    )
    description: str = Field(default="", max_length=2000)
    cover_file_id: UUID | None = None
    allow_preview: bool = True
    allow_download: bool = True
    questionnaire: Questionnaire = Questionnaire()

    @model_validator(mode="after")
    def accessible(self):
        if self.enabled and not (self.allow_preview or self.allow_download):
            raise ValueError("at least one access mode is required")
        return self


class DistributionFile(KnowledgeModel):
    file_id: UUID
    filename: str
    media_type: str
    size_bytes: int
    preview_supported: bool = Field(
        description=(
            "UTF-8 text, a signed PDF, or a signed PNG/JPEG/WebP image; "
            "allow_preview and a current grant are still required."
        )
    )


class PublicationView(KnowledgeModel):
    material_id: UUID
    publication_id: UUID | None = None
    version_id: UUID | None = None
    version_no: int | None = None
    revision: int = 0
    enabled: bool = False
    content_name: str
    description: str = ""
    cover_file_id: UUID | None = None
    allow_preview: bool = False
    allow_download: bool = False
    questionnaire: Questionnaire = Questionnaire()
    questionnaire_version: int = 0
    published_at: datetime | None = None
    content_category: ContentCategory | None = None
    product_code: str | None = None
    product_label: str | None = None
    files: tuple[DistributionFile, ...] = ()
    # Authenticated sales users may use these links to open the original source.
    # They are deliberately absent from PublicShare, which is the anonymous customer boundary.
    source_links: tuple[str, ...] = ()
    can_publish: bool = False
    can_share: bool = False


class DistributionPage(KnowledgeModel):
    source: Literal["salesbuddy.customer_distribution"] = "salesbuddy.customer_distribution"
    items: tuple[PublicationView, ...]
    total: int
    limit: int
    offset: int
    can_manage: bool


class ShareWrite(KnowledgeModel):
    publication_id: UUID


class ShareView(KnowledgeModel):
    share_id: UUID
    material_id: UUID
    publication_id: UUID
    version_id: UUID
    sales_user_id: UUID
    version_no: int | None = None
    sales_user_name: str | None = None
    content_name: str | None = None
    created_at: datetime
    revoked_at: datetime | None = None
    share_path: str | None = None
    token: str | None = None


class SharePage(KnowledgeModel):
    items: tuple[ShareView, ...]
    total: int
    limit: int
    offset: int


class RevokeWrite(KnowledgeModel):
    expected_revoked: bool = False


class PublicShare(KnowledgeModel):
    content_name: str
    description: str
    version_no: int
    publication_id: UUID
    questionnaire_version: int
    questionnaire: Questionnaire
    allow_preview: bool
    allow_download: bool
    files: tuple[DistributionFile, ...]
    has_cover: bool = False
    requires_questionnaire: bool


class AnswerValues(KnowledgeModel):
    name: str = Field(default="", max_length=100)
    company: str = Field(default="", max_length=200)
    contact: str = Field(default="", max_length=200)
    contact_intent: str = Field(default="", max_length=500)


class ResponseWrite(KnowledgeModel):
    browser_id: BrowserId
    questionnaire_version: int = Field(ge=1)
    answers: AnswerValues = AnswerValues()


class GrantView(KnowledgeModel):
    response_id: UUID
    grant_token: str
    expires_at: datetime
    publication_id: UUID
    questionnaire_version: int


class AnalyticsQuery(KnowledgeModel):
    scope: Literal["self", "all"] = "self"
    material_id: UUID | None = None
    sales_user_id: UUID | None = None
    version_id: UUID | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def valid_dates(self):
        for value in (self.date_from, self.date_to):
            if value is not None and value.tzinfo is None:
                raise ValueError("timezone required")
        if self.date_from and self.date_to and self.date_from >= self.date_to:
            raise ValueError("date_from must precede date_to")
        return self


class StatisticView(KnowledgeModel):
    source: Literal["salesbuddy.customer_distribution"] = "salesbuddy.customer_distribution"
    observed_at: datetime
    scope: Literal["self", "all"]
    visits: int
    unique_visitors_estimate: int
    questionnaire_submissions: int
    previews: int
    download_requests: int
    can_view_all: bool


class AnswerView(KnowledgeModel):
    response_id: UUID
    share_id: UUID
    material_id: UUID
    publication_id: UUID
    version_id: UUID
    sales_user_id: UUID
    version_no: int | None = None
    sales_user_name: str | None = None
    content_name: str | None = None
    questionnaire_version: int
    questionnaire: Questionnaire
    answers: AnswerValues
    submitted_at: datetime


class AnswerPage(KnowledgeModel):
    items: tuple[AnswerView, ...]
    total: int
    limit: int
    offset: int
    can_view_all: bool
