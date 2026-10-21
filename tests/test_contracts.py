"""Business edge cases that must fail before SQL publication."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from orders_pipeline.models import Order, OrderPatch


def valid_order():
    return {
        "order_id": 1,
        "customer_id": 2,
        "total_gross": "123.45",
        "currency": "PLN",
        "status": "paid",
        "updated_at": "2026-01-01T12:00:00+02:00",
    }


@pytest.mark.parametrize(
    "amount", [12.34, "NaN", "-1.00", "1.234", "1", "1,20", None, "1000000000000.00", True]
)
def test_invalid_money_is_not_rounded_or_imputed(amount):
    with pytest.raises(ValidationError):
        Order.model_validate({**valid_order(), "total_gross": amount})


def test_money_timezone_and_explicit_normalization():
    order = Order.model_validate({**valid_order(), "currency": " pln ", "status": " PAID "})
    assert order.total_gross == Decimal("123.45")
    assert order.updated_at.isoformat() == "2026-01-01T10:00:00+00:00"
    assert order.currency == "PLN" and order.status == "paid"


@pytest.mark.parametrize(
    "changes",
    [
        {"order_id": True},
        {"order_id": "1"},
        {"customer_id": 0},
        {"updated_at": "2026-01-01T12:00:00"},
        {"is_deleted": "false"},
        {"currency": "BTC"},
        {"status": "unknown"},
        {"new_field": "schema drift"},
    ],
)
def test_invalid_business_contract(changes):
    with pytest.raises(ValidationError):
        Order.model_validate({**valid_order(), **changes})


def test_zero_and_large_valid_amounts_are_preserved():
    for amount in ["0.00", "999999999999.99"]:
        assert Order.model_validate(
            {**valid_order(), "total_gross": amount}
        ).total_gross == Decimal(amount)


@pytest.mark.parametrize("patch", [{}, {"status": None}, {"is_deleted": None}])
def test_empty_or_null_patch_is_rejected(patch):
    with pytest.raises(ValidationError):
        OrderPatch.model_validate(patch)
