"""A count match alone must never approve a migration."""

from orders_pipeline.models import Change
from orders_pipeline.reconcile import compare, summarize


def change(order_id, amount="10.00", currency="PLN", deleted=False):
    return Change.model_validate(
        {
            "change_id": order_id,
            "order": {
                "order_id": order_id,
                "customer_id": 1,
                "total_gross": amount,
                "currency": currency,
                "status": "paid",
                "updated_at": "2026-01-01T00:00:00Z",
                "is_deleted": deleted,
            },
        }
    )


def test_equal_counts_with_wrong_identifiers_do_not_match():
    assert not compare([change(1)], [change(2)])["matched"]


def test_changed_business_value_is_detected():
    assert compare([change(1)], [change(1, "11.00")])["differences"]["changed_count"] == 1


def test_currency_totals_are_separate_and_exclude_tombstones():
    result = summarize([change(1), change(2, "0.10", "EUR"), change(3, deleted=True)])
    assert result == {
        "total": 3,
        "active": 2,
        "active_gross_by_currency": {"EUR": "0.10", "PLN": "10.00"},
    }


def test_empty_matching_sources_are_valid():
    assert compare([], [])["matched"] is True
