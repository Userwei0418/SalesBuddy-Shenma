"""New frozen material gets controlled business labels without changing facts."""
from uuid import uuid4

from sales_backend.domain.work_report_facts import FactAssembler, count_metric
from tests.test_work_report_repository import PERIOD, NOW


def test_known_units_and_controlled_opportunity_state_only():
    assert count_metric('tasks_completed','完成',1,definition='test').unit == '项'
    assert count_metric('opportunities_visible','商机',26,definition='test').unit == '个'
    assert count_metric('visits_completed','跟进',1,definition='test').unit == '条'
    assert count_metric('unknown_count','未知',1,definition='test').unit == '条'
    assembler = FactAssembler(period=PERIOD, data_as_of=NOW)
    raw = dict(id=str(uuid4()),version_no=1,name='Synthetic',stage_code='qualified',status='open',
               creation_date={},summary='用户原文 qualified/open 不替换',amount='100.10',currency='CNY')
    assembler.add('opportunity',[raw])
    source = assembler.result.sources[0]
    assert source.text.startswith('快照时点的当前状态：商机确认－30%；进行中。')
    assert source.text.endswith('用户原文 qualified/open 不替换')
    amounts = [m for m in assembler.result.metrics if m.kind=='amount']
    assert amounts[0].value=='100.10' and amounts[0].unit=='原币'
    assert raw['stage_code']=='qualified' and raw['status']=='open'
