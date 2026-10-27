import copy
import json
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/installation'))
from demo_business import identity
from demo_reset import mapping_value
from demo_reset_catalog import CLEARED,HISTORY_GUARDS,RETAINED,validate_structure
from instance_files import InstanceError


def valid_mapping():
    return {'administrator':str(uuid.uuid4()),'form_version':str(uuid.uuid4()),
        'accounts':{key:str(uuid.uuid4()) for key in ('DEMO_SALES_A','DEMO_SALES_B','DEMO_MANAGER','DEMO_FDE')},
        'teams':{key:str(uuid.uuid4()) for key in ('east','west','fde')}}


def test_reset_ids_are_disjoint_across_tenant_and_operation():
    tenant,other,op,again=uuid.uuid4(),uuid.uuid4(),uuid.uuid4(),uuid.uuid4()
    assert len({identity(tenant,'customer:asset'),identity(tenant,'customer:asset',op),
                identity(tenant,'customer:asset',again),identity(other,'customer:asset',op)})==4
    assert identity(tenant,'customer:asset',op)==identity(tenant,'customer:asset',str(op))


@pytest.mark.parametrize('damage',['missing','duplicate_account','duplicate_team','name','extra'])
def test_mapping_never_infers_or_creates_identity(damage):
    value=valid_mapping()
    if damage=='missing':del value['accounts']['DEMO_FDE']
    if damage=='duplicate_account':value['accounts']['DEMO_SALES_A']=value['accounts']['DEMO_SALES_B']
    if damage=='duplicate_team':value['teams']['east']=value['teams']['west']
    if damage=='name':value['accounts']['DEMO_FDE']='FDE product'
    if damage=='extra':value['password']='not accepted'
    with pytest.raises((ValueError,InstanceError)):mapping_value(value)


def test_catalog_preserves_originals_product_configuration_and_never_disables_fk():
    expected=json.loads((Path(__file__).resolve().parents[2]/'tools/installation/demo_reset_catalog.json').read_text())['structure']
    assert len(CLEARED)==45
    assert RETAINED=={'crm.product','activity.visit_import','activity.visit_import_content'}
    assert len(HISTORY_GUARDS)==6
    assert all(c['source'] in CLEARED for c in expected['constraints'] if c['target'] in CLEARED)
    assert validate_structure(expected,expected)==expected
    different=copy.deepcopy(expected);different['triggers'][0]['enabled']='D'
    assert validate_structure(different,expected)==different


@pytest.mark.parametrize('damage',['table','trigger','function','constraint','foreign_dependency','disabled_fk'])
def test_catalog_fails_closed_on_schema_drift(damage):
    expected=json.loads((Path(__file__).resolve().parents[2]/'tools/installation/demo_reset_catalog.json').read_text())['structure']
    actual=copy.deepcopy(expected)
    if damage=='disabled_fk':actual['foreign_guards'][0]['enabled']='D'
    if damage=='table':actual['tables'].append('crm.new_business')
    if damage=='trigger':actual['triggers'][0]['tgname']='unknown'
    if damage=='function':actual['triggers'][0]['function_hash']='unknown'
    if damage=='constraint':actual['constraints'][0]['definition']='unknown'
    if damage=='foreign_dependency':actual['constraints'].append({'source':'ops.example','target':'crm.customer','conname':'new_ref','definition':'FK'})
    with pytest.raises(InstanceError):validate_structure(actual,expected)
