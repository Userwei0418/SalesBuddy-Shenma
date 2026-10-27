"""Archive an exact imported source attachment without inventing CRM metadata."""

from typing import Annotated

from pydantic import Field, model_validator

from sales_backend.contracts.knowledge import KnowledgeId, KnowledgeModel, Label, Revision
from sales_backend.contracts.knowledge_import import ImportedAttachment, SourceId


class SourceFileInput(KnowledgeModel):
    source_token: SourceId
    name: Label
    size_bytes: Annotated[int, Field(strict=True, gt=0, le=52428800)]
    file_id: KnowledgeId


class SourceArchiveInput(KnowledgeModel):
    expected_material_revision: Revision
    expected_acl_revision: Revision
    expected_source_revision: Annotated[int, Field(strict=True, ge=0)]
    attachments: Annotated[tuple[SourceFileInput, ...], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def distinct(self):
        if len({a.source_token for a in self.attachments}) != len(self.attachments):
            raise ValueError("KNOWLEDGE_SOURCE_ATTACHMENT_DUPLICATE")
        if len({a.file_id for a in self.attachments}) != len(self.attachments):
            raise ValueError("KNOWLEDGE_SOURCE_FILE_DUPLICATE")
        return self


class SourceArchivedFile(SourceFileInput):
    sha256: str
    media_type: str


class SourceFilesView(KnowledgeModel):
    material_id: KnowledgeId
    version_id: KnowledgeId
    version_no: Revision
    material_revision: Revision
    acl_revision: Revision
    source_revision: int
    source_status: str
    can_archive: bool
    attachments: tuple[ImportedAttachment, ...]
    files: tuple[SourceArchivedFile, ...]
    changed: bool = False
