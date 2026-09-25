import json
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from sales_backend.services.weekly_source import exact_json, decode_snapshot, window
from sales_backend.weekly_contract.decode_response import decode_response
from sales_backend.weekly_contract.validate_response import validate, validate_input

ROOT = Path(__file__).parent/'fixtures/weekly_v2'


@pytest.mark.parametrize('path', sorted(ROOT.glob('*.json')), ids=lambda p:p.stem)
def test_frozen_handoff_cases(path):
    source=json.loads(path.read_text())
    output=json.loads((ROOT/'outputs'/path.name).read_text())
    assert validate(source, output)==[]


def test_full_precision_amount_and_shanghai_boundaries():
    value=Decimal('9999999999999999.99')
    assert decode_snapshot(exact_json({'amount':value}))['amount']==value
    period,start,end=window(datetime.fromisoformat('2026-09-23T16:00:00+00:00'))
    assert period['start_date']=='2026-09-11' and period['end_date']=='2026-09-24'
    assert start.isoformat()=='2026-09-11T00:00:00+08:00'
    assert end.isoformat()=='2026-09-25T00:00:00+08:00'


def test_cross_customer_source_and_invented_stats_rejected():
    source=json.loads((ROOT/'normal_enriched.json').read_text())
    output=json.loads((ROOT/'outputs/normal_enriched.json').read_text())
    bad=deepcopy(output);bad['statistics']['record_count']+=1
    assert validate(source,bad)
    bad=deepcopy(source);bad['records'][0]['recorder_id']='different-user'
    assert validate_input(bad)
    bad=deepcopy(output)
    bad['sections'][0]['items'][0]['customer_id']='another-customer'
    assert validate(source,bad)


@pytest.mark.parametrize('text',['{"a":1,"a":2}','{"a":NaN}','prefix {"a":1}','{"a":1} {"b":2}'])
def test_bad_json_never_becomes_a_report(text):
    with pytest.raises(ValueError):decode_response(text)


def test_report_week_is_separate_from_fourteen_day_window():
    from datetime import date
    from sales_backend.services.weekly_source import report_dates
    now=datetime.fromisoformat('2027-01-04T01:00:00+00:00')
    week=date(2026,12,28)
    period,start,end=window(now,week)
    assert period['start_date']=='2026-12-21' and period['end_date']=='2027-01-03'
    assert report_dates(now,week)[1].isoformat()=='2027-01-03T23:59:59.999999+08:00'
    for invalid in (date(2027,1,11),date(2027,1,5)):
        with pytest.raises(ValueError,match='WEEKLY_INVALID_REPORT_WEEK'):window(now,invalid)
