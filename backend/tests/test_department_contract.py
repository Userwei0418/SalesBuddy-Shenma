from uuid import uuid4
import pytest
from pydantic import ValidationError
from sales_backend.contracts.operations import AccountUpdate


def test_multi_appointment_contract_requires_a_single_declared_primary_and_exact_role_union():
    east, north = uuid4(), uuid4()
    base = dict(version_no=1, display_name="主管", status="active", team_id=east, roles=["supervisor"])
    data = AccountUpdate(
        **base,
        memberships=[dict(team_id=east, roles=["supervisor"]), dict(team_id=north, roles=["supervisor"], acting=True)],
    )
    assert data.memberships[1].acting
    for members in [
        [],
        [dict(team_id=north, roles=["supervisor"])],
        [dict(team_id=east, roles=["supervisor"]), dict(team_id=east, roles=["supervisor"])],
        [dict(team_id=east, roles=["administrator"])],
    ]:
        with pytest.raises(ValidationError):
            AccountUpdate(**base, memberships=members)
    assert AccountUpdate(**base).memberships is None
