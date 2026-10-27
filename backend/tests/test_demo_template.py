import json
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/installation'))
from demo_template import credential_receipt, identity, template
from instance_files import InstanceError


@pytest.fixture
def demo(tmp_path):
    root=tmp_path.resolve();(root/'private').mkdir(mode=0o700)
    target=root/'release/tools/installation';target.mkdir(parents=True)
    source=Path(__file__).resolve().parents[2]/'tools/installation/demo_template.json'
    (target/source.name).write_bytes(source.read_bytes())
    value,digest=template(root)
    return root,{'instance_id':str(uuid.uuid4())},uuid.uuid4(),value,digest


def test_synthetic_template_contains_four_quadrants_and_valid_owners(demo):
    _,_,_,value,_=demo
    assert {c['quadrant'] for c in value['customers']}=={'main_attack','customer_asset','order_driven','customer_resource'}
    sales={a['code'] for a in value['accounts'] if a['role']=='sales'}
    assert all(c['owner'] in sales and c['name'].startswith('合成演示') for c in value['customers'])
    assert not any(k in a for a in value['accounts'] for k in ('password','phone','email'))


def test_demo_ids_are_tenant_bound_and_reproducible():
    a,b=uuid.uuid4(),uuid.uuid4()
    assert identity(a,'customer:asset')==identity(a,'customer:asset')
    assert identity(a,'customer:asset')!=identity(b,'customer:asset')


def test_initial_passwords_are_unique_private_and_durable_without_reset(demo):
    path,receipt,digest=credential_receipt(*demo)
    assert path.stat().st_mode & 0o077 == 0
    assert path.parent.stat().st_mode & 0o077 == 0
    assert len({a['password'] for a in receipt['accounts']})==4
    assert all(a['must_change_password'] for a in receipt['accounts'])
    assert credential_receipt(*demo)==(path,receipt,digest)


@pytest.mark.parametrize('damage',['instance','company','template','short_password','public','symlink'])
def test_incompatible_or_unsafe_credential_receipt_is_never_replaced(demo,damage):
    path,receipt,_=credential_receipt(*demo)
    if damage=='instance': receipt['instance_id']=str(uuid.uuid4())
    if damage=='company': receipt['workspace_id']=str(uuid.uuid4())
    if damage=='template': receipt['template_sha256']='0'*64
    if damage=='short_password': receipt['accounts'][0]['password']='short'
    path.write_text(json.dumps(receipt))
    if damage=='public': path.chmod(0o644)
    if damage=='symlink':
        saved=path.with_suffix('.saved');path.rename(saved);path.symlink_to(saved)
    before=path.read_bytes()
    with pytest.raises((InstanceError,ValueError,OSError)):
        credential_receipt(*demo)
    assert path.read_bytes()==before
