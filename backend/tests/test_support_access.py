from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sales_backend.api.support_access import SupportApproval
from sales_backend.domain.permission_defaults import default_permissions
from sales_backend.services.platform_control import PUBLIC_FUNCTIONS


def test_support_approval_requires_an_explicit_recipient_scopes_expiry_and_reason():
    body={'id':str(uuid4()),'operator_id':str(uuid4()),'scopes':['customers'],
          'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),'reason':' investigate '}
    assert SupportApproval(**body).reason=='investigate'
    for key in body:
        with pytest.raises(ValidationError): SupportApproval(**{k:v for k,v in body.items() if k!=key})
    for delta in ({'scopes':[]},{'scopes':['customers','customers']},{'scopes':['write']},
                  {'reason':' '},{'expires_at':'2026-10-01T10:00:00'},{'workspace_id':str(uuid4())}):
        with pytest.raises(ValidationError): SupportApproval(**{**body,**delta})


def test_support_management_is_not_given_to_business_or_operations_roles():
    for role in ('sales','supervisor','manager','fde','fde_lead','operations','administrator'):
        assert ('support.manage' in {p['permission'] for p in default_permissions(role)})==(role=='administrator')
    assert 'support_read(text,uuid,text,uuid)' in PUBLIC_FUNCTIONS
    assert not any('approve_support' in f or 'revoke_support' in f for f in PUBLIC_FUNCTIONS)
