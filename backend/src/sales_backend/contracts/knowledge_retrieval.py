"""HTTP-facing contracts for the version-scoped knowledge assistant."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from sales_backend.contracts.knowledge import KnowledgeModel


class KnowledgeAskInput(KnowledgeModel):
    query: Annotated[str, Field(min_length=1, max_length=8000)]
    material_ids: tuple[UUID, ...] = Field(min_length=1, max_length=20)

    @property
    def unique_material_ids(self) -> tuple[UUID, ...]:
        return tuple(dict.fromkeys(self.material_ids))


class KnowledgeProviderBindingInput(KnowledgeModel):
    """Admin supplied identity of one provider document for a current version."""

    provider_dataset_id: Annotated[str, Field(min_length=1, max_length=300)]
    provider_document_id: Annotated[str, Field(min_length=1, max_length=300)]


class KnowledgeProviderBindingReceipt(KnowledgeModel):
    material_id: UUID
    version_id: UUID
    provider_dataset_id: str
    provider_document_id: str
    state: Literal["active", "disabled"]
    observed_at: datetime


class KnowledgeCitationView(KnowledgeModel):
    material_id: UUID
    version_id: UUID
    material_name: str
    version_no: int
    provider_document_id: str
    chunk_id: str
    location: str
    quote: str


class KnowledgeAskResponse(KnowledgeModel):
    status: Literal["answered", "no_evidence"]
    answer: str | None = None
    citations: tuple[KnowledgeCitationView, ...] = ()


class KnowledgeWorkFile(KnowledgeModel):
    """A provider-generated file only after the provider has archived it."""

    file_id: UUID
    filename: str
    media_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)


class KnowledgeWorkSource(KnowledgeModel):
    material_id: UUID
    version_id: UUID
    content_name: str | None = None


class KnowledgeWorkRecipient(KnowledgeModel):
    subject_kind: Literal["user", "team"]
    subject_id: UUID


class KnowledgeWorkRecipientView(KnowledgeWorkRecipient):
    display_name: str | None = None


class KnowledgeWorkView(KnowledgeModel):
    work_id: UUID
    owner_user_id: UUID
    can_manage: bool
    revision: int = Field(ge=1)
    recipients: tuple[KnowledgeWorkRecipientView, ...] = ()
    parent_work_id: UUID | None = None
    job_id: UUID | None = None
    title: str | None = None
    purpose: str | None = None
    format: Literal["docx", "pptx"] = "docx"
    audience: str | None = None
    status: Literal["queued", "running", "archiving", "succeeded", "failed"]
    version_no: int | None = Field(default=None, ge=1)
    error: str | None = None
    files: tuple[KnowledgeWorkFile, ...] = ()
    sources: tuple[KnowledgeWorkSource, ...] = ()
    material_id: UUID | None = None
    version_id: UUID | None = None


class KnowledgeWorkPage(KnowledgeModel):
    """Stable page shape for the Web "我的作品" view.

    The provider bridge may remain unavailable, but the HTTP contract must be
    present so the client never falls through to an untyped 404 response.
    """

    items: tuple[KnowledgeWorkView, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class KnowledgeGenerateInput(KnowledgeModel):
    purpose: str = Field(min_length=1, max_length=4000)
    material_ids: tuple[UUID, ...] = Field(min_length=1, max_length=20)
    format: Literal["docx", "pptx"]
    audience: str = Field(min_length=1, max_length=100)
    parent_work_id: UUID | None = None
    expected_parent_revision: Annotated[int, Field(strict=True, ge=1)] | None = None

    @model_validator(mode="after")
    def parent_pair(self):
        if (self.parent_work_id is None) != (self.expected_parent_revision is None):
            raise ValueError("Parent work and expected revision must be supplied together")
        return self

    @property
    def unique_material_ids(self) -> tuple[UUID, ...]:
        return tuple(dict.fromkeys(self.material_ids))


class KnowledgeGenerateReceipt(KnowledgeModel):
    job_id: UUID
    status: Literal["queued", "running", "archiving", "succeeded", "failed"]


class KnowledgePublishInput(KnowledgeModel):
    expected_revision: Annotated[int, Field(strict=True, ge=1)]
    recipients: tuple[KnowledgeWorkRecipient, ...] = Field(max_length=200)

    @model_validator(mode="after")
    def unique_recipients(self):
        if len(set(self.recipients)) != len(self.recipients):
            raise ValueError("KNOWLEDGE_RECIPIENT_DUPLICATE")
        return self


class KnowledgeWorkCandidate(KnowledgeModel):
    subject_id: UUID
    display_name: str


class KnowledgeWorkCandidatePage(KnowledgeModel):
    work_id: UUID
    subject_kind: Literal["user", "team"]
    items: tuple[KnowledgeWorkCandidate, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=100000)
