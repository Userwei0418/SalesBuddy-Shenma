from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from sales_backend.domain.feishu_sync.config import SyncConfig
from sales_backend.repositories.feishu_sync import FeishuRepository
from tests.test_feishu_sync_policy import payload


@pytest.mark.asyncio
@pytest.mark.parametrize('confirmed,live,credential,expected', [
    (False, False, True, '确认迁移'),
    (True, True, True, '暂停同步'),
    (True, False, False, '新应用凭证'),
    (True, False, True, None),
])
async def test_target_migration_requires_explicit_paused_flow(confirmed, live, credential, expected):
    prior = payload()
    candidate = {**prior, 'revision': 2, 'app_id': 'cli_new', 'enabled': False}
    config = SyncConfig.model_validate(candidate)
    repo = FeishuRepository()
    repo.config = AsyncMock(return_value={
        'revision': 1, 'id': UUID(prior['connection_id']), 'enabled': live, 'settings': prior})
    connection = SimpleNamespace(execute=AsyncMock())
    actor = SimpleNamespace(workspace_id=prior['workspace_id'], user_id=str(UUID(int=8)))
    if expected:
        with pytest.raises(ValueError, match=expected):
            await repo.save(connection, actor, config, 1, migrate_target=confirmed, new_credential=credential)
        assert not any('prepare_feishu_migration' in call.args[0] for call in connection.execute.await_args_list)
    else:
        await repo.save(connection, actor, config, 1, migrate_target=confirmed, new_credential=credential)
        calls = connection.execute.await_args_list
        assert 'prepare_feishu_migration' in calls[-2].args[0]
        assert 'INSERT INTO config.feishu_connection' in calls[-1].args[0]
