"""Upload acknowledgement state, independent of the current processing result."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

ImportStatus = Literal['queued', 'processing', 'succeeded', 'failed']


class LegacyUploadReceipt(BaseModel):
    id: UUID
    status: ImportStatus
    filename: str


class UploadRegistered(LegacyUploadReceipt):
    upload_intent_id: UUID
    state: Literal['registered']
    replayed: bool


class UploadPending(BaseModel):
    upload_intent_id: UUID
    state: Literal['processing', 'uncertain', 'retryable_failure']
    filename: str
    id: None = None
    status: None = None
    retryable: bool
    retry_after_seconds: int


class UploadError(BaseModel):
    detail: str
    code: Literal['UPLOAD_INTENT_NOT_FOUND', 'UPLOAD_INTENT_CONFLICT', 'UPLOAD_STATUS_UNAVAILABLE',
                  'UPLOAD_TOO_LARGE', 'INVALID_UPLOAD', 'STORAGE_QUOTA_EXCEEDED']
