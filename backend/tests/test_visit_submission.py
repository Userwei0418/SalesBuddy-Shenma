"""Current visit requirements, rather than the retired sixteen-field validator."""
import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

from sales_backend.contracts.visit_schema import FIRST_VISIT_KEYS, schema_snapshot
from sales_backend.domain.visit_contract import ensure_visit_result, validate_content
from sales_backend.services.visit_review_gate import consume_review
from sales_backend.domain.visit_review import review_text
from .test_visit_review_gate import ACTOR, FIELDS, connection


@pytest.mark.parametrize('score,accepted',[(0,False),(60,False),(61,True),(69,True),(70,True),(100,True),(101,False),(True,False)])
def test_database_review_score_is_strictly_greater_than_sixty(score,accepted):
    db=connection(payload={'quality_review':{'follow_up_score':score,'next_action':{'passed':True}}})
    if accepted:
        values,_=asyncio.run(consume_review(db,ACTOR,'customer',FIELDS))
        assert values['_follow_up_quality_score']==score
    else:
        with pytest.raises(ValueError): asyncio.run(consume_review(db,ACTOR,'customer',FIELDS))
        db.execute.assert_not_awaited()


def test_first_visit_keeps_unknown_budget_as_evidence_and_requires_human_fields():
    fields={**FIELDS,'is_first_visit':True,'customer_main_business':'设备制造','customer_needs':'减少客服查资料时间',
        'customer_budget':'客户未确认预算，需下次沟通','contact_role':'影响者'}
    assert validate_content(fields)['customer_budget']==fields['customer_budget']
    for key in FIRST_VISIT_KEYS:
        with pytest.raises(ValueError,match='首次拜访'): validate_content({**fields,key:''})
    with pytest.raises(ValueError,match='联系人角色'): validate_content({**fields,'contact_role':'IT总监'})
    assert validate_content(FIELDS)['is_first_visit'] is False
    result=ensure_visit_result({'fields':fields,'quality_review':{'follow_up_score':85,'next_action':{'passed':True}}})
    assert result['fields']['customer_needs']==fields['customer_needs']
    assert result['fields']['is_first_visit'] is True
    missing=ensure_visit_result({'fields':{**fields,'customer_budget':''},
        'quality_review':{'follow_up_score':85,'next_action':{'passed':True}}})
    assert '客户预算' in missing['missing_fields']


def test_frontend_and_backend_review_text_match_including_first_visit():
    root=Path(__file__).resolve().parents[2]
    script="const f=require('./frontend/miniprogram/utils/visitFlow');process.stdout.write(f.reviewText(JSON.parse(process.argv[1])));"
    for fields in [FIELDS,{**FIELDS,'is_first_visit':True,**{k:'人工确认' for k in FIRST_VISIT_KEYS}}]:
        actual=subprocess.check_output(['node','-e',script,json.dumps(fields)],cwd=root,text=True)
        assert actual==review_text(fields)


def test_current_fields_have_a_generated_frozen_database_version():
    root=Path(__file__).resolve().parents[2]
    spec=importlib.util.spec_from_file_location('export_visit_contract',root/'backend/scripts/export_visit_contract.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.TARGET.read_text()==module.render()
    snapshot=schema_snapshot()
    assert snapshot['threshold']=={'operator':'>','value':60}
    assert len({f['field_key'] for f in snapshot['fields']})==len(snapshot['fields'])
