"""Production range predicates at Shanghai day/year boundaries under real RLS."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from sales_backend.repositories.dashboard import DashboardRepository
from sales_backend.repositories.profile_performance import ProfilePerformanceRepository
from sales_backend.repositories.visits import VisitRepository
from tests.integration.test_customer_assets import customer
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio
CHINA = ZoneInfo('Asia/Shanghai')

async def record(connection, person, customer_id, instant, score=80):
    value = await VisitRepository().create(connection, person, customer_id=customer_id, fields={
        'interaction_at': instant.date().isoformat(), 'created_date': instant.date().isoformat(),
        'contact_name': '区间联系人',
        'follow_up_record': '日期区间真实数据库测试', 'next_action': '本周确认后续计划',
        '_follow_up_quality_score': score,
    })
    await connection.execute('UPDATE activity.visit SET interaction_at=$2,follow_up_score=$3 WHERE id=$1::uuid', value['id'], instant, score)
    return value['id']

async def test_recent_visit_day_bounds_include_today_future_time_but_not_tomorrow(connection):
    person = await actor(connection,'XS001'); c = await customer(connection,person)
    today = await connection.fetchval("SELECT (statement_timestamp() AT TIME ZONE 'Asia/Shanghai')::date")
    start = datetime.combine(today-timedelta(days=6),time.min,CHINA)
    end = datetime.combine(today+timedelta(days=1),time.min,CHINA)
    outside_before = await record(connection,person,c['id'],start-timedelta(microseconds=1))
    included_start = await record(connection,person,c['id'],start)
    included_end = await record(connection,person,c['id'],end-timedelta(microseconds=1))
    outside_after = await record(connection,person,c['id'],end)
    rows_by_zone = []
    for zone in ('UTC','Asia/Shanghai','America/Los_Angeles'):
        await connection.execute("SELECT set_config('TimeZone',$1,true)",zone)
        rows = await DashboardRepository().recent_visits(connection,person)
        ids = {r['id'] for r in rows};rows_by_zone.append(ids)
        assert {included_start,included_end} <= ids
        assert outside_before not in ids and outside_after not in ids
    assert rows_by_zone[0] == rows_by_zone[1] == rows_by_zone[2]
    other = await actor(connection,'XS002')
    assert not {included_start,included_end} & {r['id'] for r in await DashboardRepository().recent_visits(connection,other)}

@pytest.mark.parametrize('start_date,end_date',[(date(2025,12,31),date(2026,1,1)),(date(2026,3,31),date(2026,4,1))])
async def test_profile_score_inclusive_dates_and_empty_inverted_range(connection,start_date,end_date):
    person=await actor(connection,'XS001');c=await customer(connection,person)
    start=datetime.combine(start_date,time.min,CHINA);end=datetime.combine(end_date+timedelta(days=1),time.min,CHINA)
    for instant,score in [(start-timedelta(microseconds=1),0),(start,80),(end-timedelta(microseconds=1),100),(end,0)]:
        await record(connection,person,c['id'],instant,score)
    scope={'scope':'person','team_ids':[]}
    expected=await connection.fetchval("""SELECT avg(follow_up_score) FROM activity.visit
      WHERE recorder_user_ref_id=$1::uuid AND deleted_at IS NULL AND status IN ('confirmed','archived')
      AND timezone('Asia/Shanghai',interaction_at)::date BETWEEN $2 AND $3""",person.user_id,start_date,end_date)
    assert expected is not None and expected>0
    values=[]
    for zone in ('UTC','Asia/Shanghai','America/Los_Angeles'):
        await connection.execute("SELECT set_config('TimeZone',$1,true)",zone)
        result=await ProfilePerformanceRepository().facts(connection,[person.user_id],end_date.year,end_date,start_date,scope,target_start=start_date,target_end=end_date)
        assert result['followup_score'] == expected
        values.append(result)
    assert values[0] == values[1] == values[2]
    empty=await ProfilePerformanceRepository().facts(connection,[person.user_id],end_date.year,end_date,end_date+timedelta(days=1),scope,target_start=start_date,target_end=end_date)
    assert empty['followup_score'] is None
