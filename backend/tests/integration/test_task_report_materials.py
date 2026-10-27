"""One focused report-material query under the disposable runtime RLS role."""
from datetime import UTC, date, datetime

import pytest

from sales_backend.contracts.work_report import PeriodSelection
from sales_backend.domain.work_report import resolve_period
from sales_backend.domain.work_report_facts import FactAssembler
from sales_backend.repositories.work_report_facts import source_query
from tests.integration.feishu_fixtures import assert_restricted, seed_execute
from tests.integration.test_operations_claims_sql import actor


@pytest.mark.asyncio
async def test_task_materials_keep_progress_stop_and_completion_scope(connection):
    person = await actor(connection, 'XS001')
    other = await actor(connection, 'XS002')
    await seed_execute(connection, """WITH inserted AS (
      INSERT INTO workflow.task(workspace_id,creator_user_ref_id,creator_team_id,title,description,
        association_kind,status,execution_mode,created_at,due_at,completed_at,completion_note)
      SELECT $1::uuid,$2::uuid,$3::uuid,'报告任务'||n,'合成任务说明','daily',
        CASE n WHEN 1 THEN 'completed' WHEN 2 THEN 'completed' WHEN 3 THEN 'in_progress'
          WHEN 4 THEN 'deferred' WHEN 5 THEN 'needs_adjustment' WHEN 6 THEN 'pending_review'
          WHEN 7 THEN 'cancelled' WHEN 8 THEN 'completed' ELSE 'in_progress' END,
        CASE WHEN n<=5 THEN 'execution_v3' ELSE 'legacy' END,
        '2026-10-01 00:00+08','2026-10-31 00:00+08',
        CASE WHEN n IN (1,2) THEN '2026-10-24 12:00+08'::timestamptz
          WHEN n=8 THEN '2026-10-01 12:00+08'::timestamptz END,
        CASE WHEN n=6 THEN '旧任务提交说明' ELSE '已有完成说明' END
      FROM generate_series(1,9) n RETURNING id,workspace_id,title)
      INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role)
      SELECT id,workspace_id,CASE WHEN title='报告任务9' THEN $4::uuid ELSE $2::uuid END,$3::uuid,'sales' FROM inserted""",
      person.workspace_id, person.user_id, person.team_ids[0], other.user_id)
    await seed_execute(connection, """INSERT INTO workflow.task_execution(task_id,workspace_id,state)
      SELECT id,workspace_id,jsonb_build_object('protocol_version',3,
        'completion_result',CASE WHEN title='报告任务1' THEN '{"action":"close","note":"客户取消计划，停止投入"}'::jsonb
          WHEN title='报告任务2' THEN '{"action":"review_completion","note":"验证材料已验收"}'::jsonb END,
        'latest_feedback',CASE WHEN title IN ('报告任务3','报告任务4','报告任务5') THEN '{"text":"已完成环境准备，验证待确认"}'::jsonb END,
        'next_step',CASE WHEN title IN ('报告任务3','报告任务4','报告任务5') THEN '确认验证时间' END,
        'processing_reason',CASE WHEN title='报告任务5' THEN 'awaiting_review' END,
        'processing_detail',CASE WHEN title='报告任务5' THEN '{"note":"提交材料待验收"}'::jsonb END,
        'defer',CASE WHEN title='报告任务4' THEN '{"reason":"客户调整验证时间"}'::jsonb END)
      FROM workflow.task WHERE title IN ('报告任务1','报告任务2','报告任务3','报告任务4','报告任务5')""")
    await seed_execute(connection, """INSERT INTO workflow.task_event(workspace_id,task_id,event_type,actor_user_ref_id,note)
      SELECT workspace_id,id,'append_feedback',$1::uuid,'旧任务最近反馈' FROM workflow.task WHERE title='报告任务6'""", person.user_id)
    await actor(connection, 'XS001')
    await assert_restricted(connection)
    rows = [dict(row) for row in await connection.fetch(source_query('task'), 'self', [person.user_id], None)]
    assert {row['title'] for row in rows} == {f'报告任务{n}' for n in range(1, 9) if n != 7}
    weekly_rows = await connection.fetch(source_query('task', sales_week=True), 'self', [person.user_id], None)
    assert {row['id'] for row in weekly_rows} == {row['id'] for row in rows}
    now = datetime(2026, 10, 25, tzinfo=UTC)
    assembler = FactAssembler(period=resolve_period(PeriodSelection(kind='day', anchor_date=date(2026, 10, 24)), now=now), data_as_of=now)
    assembler.add('task', [*rows, rows[0]])
    sources = {source.title: source for source in assembler.result.sources}
    metrics = {metric.metric_id: metric for metric in assembler.result.metrics}
    assert metrics['tasks_completed'].value == '2'
    assert metrics['tasks_progressing'].value == '4'
    assert '报告任务8' not in sources and '报告任务9' not in sources
    assert '不再继续，并非验收通过或目标达成' in sources['报告任务1'].text
    assert '客户取消计划，停止投入' in sources['报告任务1'].text
    assert '验证材料已验收' in sources['报告任务2'].text
    for name in ('报告任务3', '报告任务4', '报告任务5'):
        assert '最新反馈：已完成环境准备，验证待确认' in sources[name].text
        assert '已登记下一步：确认验证时间' in sources[name].text
        assert sources[name].state_kind == 'current' and sources[name].business_at is None
    assert '延期原因：客户调整验证时间' in sources['报告任务4'].text
    assert '处理原因：待验收' in sources['报告任务5'].text
    assert '处理说明：提交材料待验收' in sources['报告任务5'].text
    assert '仍待下发人验收' in sources['报告任务5'].text
    assert '最近记录原文：旧任务最近反馈' in sources['报告任务6'].text
    assert '处理说明：旧任务提交说明' in sources['报告任务6'].text
    await actor(connection, 'XS002')
    scoped = await connection.fetch(source_query('task'), 'self', [other.user_id], None)
    assert [row['title'] for row in scoped] == ['报告任务9']
