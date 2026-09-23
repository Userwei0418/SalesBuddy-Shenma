"""The CLI cannot silently run as an owner, superuser, or a different company."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.maintenance.feishu_test_archive import ArchiveManifest, ArchiveRejected, archive
from tests.test_feishu_test_archive_manifest import manifest_data


def actor_for(manifest, role='administrator', workspace=None):
    return SimpleNamespace(workspace_id=workspace or str(manifest.workspace_id),
                           role=SimpleNamespace(value=role))


@pytest.mark.asyncio
@pytest.mark.parametrize('role,workspace', [('sales',None), ('operations',None), ('administrator','other')])
async def test_role_and_company_mismatch_never_reaches_database(role,workspace):
    manifest = ArchiveManifest.model_validate(manifest_data())
    connection = SimpleNamespace(fetchval=AsyncMock())
    with pytest.raises(ArchiveRejected,match='WORKSPACE_OR_ROLE_MISMATCH'):
        await archive(connection,actor_for(manifest,role,workspace),manifest)
    connection.fetchval.assert_not_called()


@pytest.mark.asyncio
async def test_elevated_login_never_invokes_archive_function():
    manifest = ArchiveManifest.model_validate(manifest_data())
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=True))
    with pytest.raises(ArchiveRejected,match='NORMAL_APPLICATION_ROLE_REQUIRED'):
        await archive(connection,actor_for(manifest),manifest)
    assert connection.fetchval.await_count == 1


@pytest.mark.asyncio
async def test_database_rejection_is_not_reported_as_success():
    manifest = ArchiveManifest.model_validate(manifest_data())
    connection = SimpleNamespace(fetchval=AsyncMock(side_effect=[False,{'code':'UNLISTED_CHILD_RECORD'}]))
    with pytest.raises(ArchiveRejected,match='UNLISTED_CHILD_RECORD'):
        await archive(connection,actor_for(manifest),manifest,expected_plan='prior-digest')
