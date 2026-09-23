import os
import re
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.agent import AgentMode, RoleCode
from sales_backend.repositories.assistant import AssistantRepository


@pytest.mark.asyncio
async def test_report_reloads_saved_run_and_rejects_old_role_scope(connection, sales_actor):
    repo=AssistantRepository()
    conversation=await repo.create_conversation(connection,sales_actor,mode=AgentMode.OPERATING_REPORT,customer_id=None)
    run_id=await repo.enqueue_message(connection,sales_actor,conversation_id=conversation['id'],
        text='当前经营报告',client_message_id=str(uuid4()),input_source='text')
    await connection.execute("INSERT INTO agent.message(workspace_id,conversation_id,sender_type,content_type,text_content,structured_content,source_run_id) VALUES($1::uuid,$2::uuid,'assistant','card','已完成',$3,$4::uuid)",
        sales_actor.workspace_id,conversation['id'],{'run_id':run_id,'title':'已入库报告','safe_customers':[{'title':'测试客户'}]},run_id)
    artifact = await connection.fetchval("INSERT INTO agent.artifact(workspace_id,conversation_id,run_id,artifact_type,schema_code,schema_version,payload,created_by_user_ref_id) VALUES($1::uuid,$2::uuid,$3::uuid,'report','report.v1',1,'{}',$4::uuid) RETURNING id",
        sales_actor.workspace_id, conversation['id'], run_id, sales_actor.user_id)
    result=await repo.get_run(connection,run_id=run_id)
    assert result['result']['title']=='已入库报告'
    # Same account, different current role: cannot revive a wider historical report.
    await set_request_context(connection,sales_actor.model_copy(update={'role':RoleCode.MANAGER}))
    assert await repo.get_run(connection,run_id=run_id) is None
    assert not await connection.fetchval('SELECT id FROM agent.artifact WHERE id=$1', artifact)
    messages=await repo.list_messages(connection,conversation_id=conversation['id'],limit=50)
    assert all(m['sender_type']!='assistant' for m in messages)
    await set_request_context(connection,sales_actor)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute('UPDATE agent.run SET fact_scope_version=1 WHERE id=$1::uuid',run_id)
    # Only this disposable-database harness may simulate a migration-invalidated historical row.
    test_role = os.environ.get('SALES_TEST_ROLE', '')
    if not re.fullmatch(r'salegent_verify_role_[a-f0-9]+', test_role):
        return
    await connection.execute('RESET ROLE')
    await connection.execute('UPDATE agent.run SET fact_scope_version=1 WHERE id=$1::uuid',run_id)
    await connection.execute(f'SET LOCAL ROLE "{test_role}"')
    assert await repo.get_run(connection,run_id=run_id) is None
    assert not await connection.fetchval('SELECT id FROM agent.artifact WHERE id=$1', artifact)
    assert all(m['sender_type']!='assistant' for m in
        await repo.list_messages(connection,conversation_id=conversation['id'],limit=50))


@pytest.mark.asyncio
async def test_assistant_results_require_same_conversation_run(connection, sales_actor):
    repo = AssistantRepository()
    first = await repo.create_conversation(connection, sales_actor, mode=AgentMode.OPERATING_REPORT, customer_id=None)
    second = await repo.create_conversation(connection, sales_actor, mode=AgentMode.OPERATING_REPORT, customer_id=None)
    run_id = await repo.enqueue_message(connection, sales_actor, conversation_id=first['id'],
        text='报告', client_message_id=str(uuid4()), input_source='text')
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with connection.transaction():
            await connection.execute("INSERT INTO agent.message(workspace_id,conversation_id,sender_type,content_type,source_run_id) VALUES($1::uuid,$2::uuid,'assistant','card',$3::uuid)",
                sales_actor.workspace_id, second['id'], run_id)
    with pytest.raises(asyncpg.CheckViolationError):
        async with connection.transaction():
            await connection.execute("INSERT INTO agent.message(workspace_id,conversation_id,sender_type,content_type) VALUES($1::uuid,$2::uuid,'assistant','card')",
                sales_actor.workspace_id, first['id'])


@pytest.mark.asyncio
async def test_raw_transcript_is_owned_input_without_generated_analysis_scope(connection, sales_actor):
    from types import SimpleNamespace
    from sales_backend.repositories.audio_artifacts import save_transcript

    artifact = str(uuid4())
    await save_transcript(connection, sales_actor, artifact,
        SimpleNamespace(text='本人的原始录音文字', duration_seconds=10, trace_id='test'),
        'visit', 'test.wav', 'test-provider')
    assert await connection.fetchval('SELECT payload->>\'text\' FROM agent.artifact WHERE id=$1::uuid', artifact) == '本人的原始录音文字'
    other = await connection.fetchval("SELECT id::text FROM platform.user_ref WHERE workspace_id=$1::uuid AND id<>$2::uuid LIMIT 1",sales_actor.workspace_id,sales_actor.user_id)
    await set_request_context(connection, sales_actor.model_copy(update={'user_id': other}))
    assert not await connection.fetchval('SELECT id FROM agent.artifact WHERE id=$1::uuid', artifact)
