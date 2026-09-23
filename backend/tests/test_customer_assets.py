from datetime import date, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sales_backend.api.customer_assets import ActualCreate
from sales_backend.repositories.customer_assets import period_start, today


def payload(**overrides):
    return dict(
        customer_id=uuid4(),
        kind="recognized",
        amount="0",
        occurred_on=today(),
        source_ref="  ledger:1  ",
        request_id=uuid4(),
        confirmed=True,
        **overrides,
    )


def test_period_and_zero_are_explicit():
    assert period_start("year", date(2027, 1, 1)) == date(2027, 1, 1)
    assert period_start("all", date(2027, 1, 1)) is None
    body = ActualCreate(**payload())
    assert body.amount == 0 and body.source_ref == "ledger:1"


@pytest.mark.parametrize(
    "change",
    [
        dict(amount="-1"),
        dict(confirmed=False),
        dict(amount="NaN"),
        dict(source_ref=" "),
        dict(occurred_on=today() + timedelta(days=1)),
    ],
)
def test_invalid_actuals(change):
    data = payload()
    data.update(change)
    with pytest.raises(ValidationError):
        ActualCreate(**data)
