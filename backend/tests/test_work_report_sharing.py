"""Share CAS/confirmation behavior; actual RLS/savepoint checks are in the PG test."""
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from sales_backend.contracts.work_report import SharePreviewRequest, ShareRequest
from sales_backend.domain.work_report import WorkReportError
from sales_backend.services import work_report_sharing as sharing
from tests.test_work_report_repository import ACTOR, READER, NOW, prepare, generate, store  # noqa: F401


def test_result_issue_uses_public_allowlist_not_stored_message():
    from sales_backend.repositories.work_report import result_issue
    issue = result_issue({'status': 'basic_summary', 'trace': {'generation_issue': {
        'code': 'AI_OUTPUT_INVALID', 'message': 'UNTRUSTED PROVIDER TRACE'}}})
    assert issue['code'] == 'AI_OUTPUT_INVALID' and 'UNTRUSTED' not in issue['message']
    assert result_issue({'status': 'generated'}) is None
    assert result_issue({'status': 'basic_summary'})['code'] == 'AI_RESULT_UNAVAILABLE'


def test_run_view_trace_uses_same_safe_diagnostic_without_mutating_stored_result():
    from sales_backend.repositories.work_report import run_view
    row = dict(id=UUID(int=1), snapshot_id=UUID(int=2), status='succeeded', version_no=1, created_at=NOW,
        result=dict(status='basic_summary', provider='none', coverage_status='complete', trace=dict(
            prompt_version='test', elapsed_ms=1, chunks_total=1, chunks_completed=0,
            generation_issue=dict(code='AI_OUTPUT_INVALID', message='UNTRUSTED PROVIDER TRACE'))))
    view = run_view(row)
    assert 'UNTRUSTED' not in view.model_dump_json()
    assert view.trace.generation_issue == view.generation_issue
    assert row['result']['trace']['generation_issue']['message'] == 'UNTRUSTED PROVIDER TRACE'
    row['result']['status'] = 'generated'
    assert run_view(row).trace.generation_issue is None


@pytest.mark.asyncio
async def test_partial_preview_confirmation_and_recheck_not_silent_filter(store, monkeypatch):
    repo, c = store
    scheme, snapshot = await prepare(repo, c)
    run, _, _ = await generate(repo, c, scheme, snapshot)
    other = str(UUID(int=4))
    original = c.fetch
    async def fetch(sql, *args):
        rows = await original(sql, *args)
        if sql == sharing.RECIPIENTS_SQL:
            rows.append({**rows[0], 'recipient_id': other, 'user_id': other})
        return rows
    c.fetch = fetch
    check = AsyncMock(side_effect=lambda _c, actor, *_: (actor.user_id == READER.user_id, 'v1'))
    monkeypatch.setattr(sharing, 'recipient_access', check)
    body = dict(version_no=1, audience=dict(kind='company'))
    preview = await repo.share_preview(c, ACTOR, run.document_id, SharePreviewRequest(**body))
    assert (preview.recipient_count, preview.readable_count, preview.unreadable_count) == (2, 1, 1)
    assert preview.can_share and preview.confirmation_required
    with pytest.raises(WorkReportError, match='重新确认') as error:
        await repo.share(c, ACTOR, run.document_id, ShareRequest(**body, confirm=True))
    assert error.value.code == 'WORK_REPORT_SHARE_CONFIRMATION_REQUIRED'
    assert c.rows['document'][str(run.document_id)]['shared_revision_id'] is None
    check.side_effect = lambda _c, actor, *_: (actor.user_id == READER.user_id, 'v2')
    with pytest.raises(WorkReportError) as error:
        await repo.share(c, ACTOR, run.document_id, ShareRequest(**body, confirm=True, visibility_confirmation=preview.preview_hash))
    assert error.value.code == 'WORK_REPORT_SHARE_CONFIRMATION_REQUIRED'
    fresh = await repo.share_preview(c, ACTOR, run.document_id, SharePreviewRequest(**body))
    result = await repo.share(c, ACTOR, run.document_id, ShareRequest(**body, confirm=True, visibility_confirmation=fresh.preview_hash))
    assert result.audience.kind == 'company'  # no silent audience narrowing
    assert result.share_visibility.readable_count == 1 and result.share_visibility.unreadable_count == 1
    assert (await repo.get_document(c, ACTOR, run.document_id)).share_visibility is None


@pytest.mark.asyncio
async def test_zero_readable_recipients_never_share(store, monkeypatch):
    repo, c = store
    scheme, snapshot = await prepare(repo, c)
    run, _, _ = await generate(repo, c, scheme, snapshot)
    monkeypatch.setattr(sharing, 'recipient_access', AsyncMock(return_value=(False, 'v1')))
    body = dict(version_no=1, audience=dict(kind='company'))
    preview = await repo.share_preview(c, ACTOR, run.document_id, SharePreviewRequest(**body))
    assert not preview.can_share and preview.reason == 'NO_RECIPIENT'
    with pytest.raises(WorkReportError) as error:
        await repo.share(c, ACTOR, run.document_id, ShareRequest(**body, confirm=True, visibility_confirmation=preview.preview_hash))
    assert error.value.code == 'WORK_REPORT_SHARE_NO_RECIPIENT'
    assert c.rows['document'][str(run.document_id)]['shared_revision_id'] is None
