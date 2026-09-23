"""One-time, fingerprint-bound compatibility archive for the notification smoke test."""
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from .feishu_test_archive import ArchiveRejected


class NotificationTestDescriptor(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    company_reference: str = Field(min_length=1, max_length=200)
    contact_name: str = Field(min_length=1, max_length=200)
    customer_name: str = Field(min_length=1, max_length=200)
    created_day: date
    industry: str = Field(min_length=1, max_length=200)
    opportunity_name: str = Field(min_length=1, max_length=200)
    visit_marker: str = Field(min_length=1, max_length=200)


async def archive(connection, actor, descriptor: NotificationTestDescriptor, *, expected_plan=None):
    """Call the fixed-fingerprint database boundary in the caller's transaction."""
    if actor.role.value != 'administrator':
        raise ArchiveRejected('WORKSPACE_OR_ROLE_MISMATCH')
    result = await connection.fetchval(
        'SELECT security.feishu_notification_test_archive_v2($1::jsonb,$2::text)',
        descriptor.model_dump(mode='json'), expected_plan)
    if not isinstance(result, dict):
        raise ArchiveRejected('INVALID_ARCHIVE_RESPONSE')
    if result.get('code'):
        raise ArchiveRejected(result['code'])
    return result
