"""A bounded, receipt-based retirement command for authorized integration data.

The database owns complete dependency visibility and atomic validation/write.
The normal application login remains subject to ordinary RLS everywhere else.
"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ArchiveRejected(ValueError):
    pass


class RecordReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    kind: Literal['customer', 'opportunity', 'visit', 'contact']
    id: UUID
    version: int = Field(ge=1)
    creation_request_id: UUID
    creator_id: UUID


class MutationReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    request_id: UUID
    actor_id: UUID


class ArchiveManifest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    workspace_id: UUID
    creator_id: UUID
    run_id: UUID
    started_at: datetime
    ended_at: datetime
    records: tuple[RecordReceipt, ...] = Field(min_length=1, max_length=8)
    mutations: tuple[MutationReceipt, ...] = Field(default=(), max_length=16)
    reason: str = Field(min_length=10, max_length=300)

    @property
    def prefix(self):
        return f'飞书联调测试-{self.run_id.hex[:8]}'

    @model_validator(mode='after')
    def bounded(self):
        if (self.started_at.tzinfo is None or self.ended_at.tzinfo is None
                or not 0 < (self.ended_at-self.started_at).total_seconds() <= 86400):
            raise ValueError('A timezone-aware creation window of at most 24 hours is required')
        if len({r.request_id for r in self.mutations}) != len(self.mutations):
            raise ValueError('Explicit unique mutation request IDs are required')
        kinds = [r.kind for r in self.records]
        if (kinds.count('customer') != 1 or kinds.count('opportunity') > 1 or kinds.count('visit') > 1
                or ('visit' in kinds and 'opportunity' not in kinds)
                or len({r.id for r in self.records}) != len(self.records)):
            raise ValueError('One customer and at most one opportunity/visit; explicit unique contact IDs')
        return self



async def archive(connection, actor, manifest, *, expected_plan=None):
    """Call the audited database boundary in the caller's business transaction."""
    if str(manifest.workspace_id) != actor.workspace_id or actor.role.value != 'administrator':
        raise ArchiveRejected('WORKSPACE_OR_ROLE_MISMATCH')
    elevated = await connection.fetchval("""SELECT rolsuper OR rolbypassrls OR EXISTS(
      SELECT 1 FROM pg_class WHERE oid=ANY($1::regclass[]) AND relowner=r.oid)
      FROM pg_roles r WHERE rolname=current_user""",
      ['crm.customer', 'crm.opportunity', 'activity.visit', 'crm.contact'])
    if elevated:
        raise ArchiveRejected('NORMAL_APPLICATION_ROLE_REQUIRED')
    result = await connection.fetchval(
        'SELECT security.feishu_test_archive($1::jsonb,$2::text)',
        manifest.model_dump(mode='json'), expected_plan)
    if not isinstance(result, dict):
        raise ArchiveRejected('INVALID_ARCHIVE_RESPONSE')
    if result.get('code'):
        raise ArchiveRejected(result['code'])
    return result
