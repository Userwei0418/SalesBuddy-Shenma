from uuid import uuid4
import pytest
from pydantic import ValidationError
from sales_backend.contracts.operations import AccountCreate, AccountUpdate

BASE = dict(display_name='Test', team_id=uuid4(), roles=['sales'])

@pytest.mark.parametrize('account', ['person5@example.com', 'person+tag@example.co.uk', 'OLD001'])
def test_supported_login_identifiers(account):
    assert AccountCreate(**BASE, account_code=account, temporary_password='Isolated-Test-2026').account_code == account
    assert AccountUpdate(**BASE, account_code=account, version_no=1, status='active').account_code == account

@pytest.mark.parametrize('account', ['x@', '@example.com', 'x y@example.com', 'x@@example.com', 'x@example..com@bad', 'x'*65])
def test_reject_malformed_identifiers(account):
    with pytest.raises(ValidationError):
        AccountUpdate(**BASE, account_code=account, version_no=1, status='active')

def test_old_clients_can_edit_without_renaming_account():
    assert AccountUpdate(**BASE, version_no=1, status='active').account_code is None


@pytest.mark.parametrize('phone,expected', [(None,None), ('',None), ('  ',None), (' 13800000001 ','13800000001')])
def test_optional_phone_normalization(phone, expected):
    body=AccountUpdate(**BASE, phone_number=phone, version_no=1, status='active')
    assert body.phone_number == expected
    assert 'phone_number' in body.model_dump(exclude_unset=True)

@pytest.mark.parametrize('phone', ['12345','12800000001','138000000011','+8613800000001','138 0000 0001'])
def test_reject_invalid_phone(phone):
    with pytest.raises(ValidationError):
        AccountCreate(**BASE, account_code='person@example.com', temporary_password='Isolated-Test-2026', phone_number=phone)

def test_omitted_phone_is_not_an_instruction_to_clear():
    body=AccountUpdate(**BASE, version_no=1, status='active')
    assert 'phone_number' not in body.model_dump(exclude_unset=True)

@pytest.mark.parametrize('email,expected', [(None,None),('',None),('  ',None),(' person@example.com ','person@example.com')])
def test_independent_contact_email_normalization(email,expected):
    assert AccountCreate(**BASE,account_code='person5',temporary_password='Isolated-Test-2026',email=email).email == expected

@pytest.mark.parametrize('email',['not-email','a@@example.com','a b@example.com','a@example','a'*255+'@example.com'])
def test_invalid_contact_email_rejected(email):
    with pytest.raises(ValidationError):
        AccountUpdate(**BASE,version_no=1,status='active',email=email)

def test_omitted_contact_email_preserves_existing_value():
    assert 'email' not in AccountUpdate(**BASE,version_no=1,status='active').model_dump(exclude_unset=True)
