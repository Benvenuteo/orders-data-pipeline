"""Exact source-to-target reconciliation at the published change boundary."""

from collections import defaultdict
from decimal import Decimal

from orders_pipeline.client import ApiClient
from orders_pipeline.db import SqlTarget
from orders_pipeline.errors import CheckpointConflict
from orders_pipeline.models import Change


def summarize(changes: list[Change]) -> dict:
    """Keep currencies separate and expose active counts including zero-order cases."""
    amounts: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    active = 0
    for change in changes:
        if not change.order.is_deleted:
            active += 1
            amounts[change.order.currency] += change.order.total_gross
    return {
        "total": len(changes),
        "active": active,
        "active_gross_by_currency": {
            key: format(value, ".2f") for key, value in sorted(amounts.items())
        },
    }


def compare(source: list[Change], target: list[Change]) -> dict:
    """Compare identifiers and every business field, not just counts and totals."""
    left = {item.order.order_id: item.model_dump(mode="json") for item in source}
    right = {item.order.order_id: item.model_dump(mode="json") for item in target}
    missing = sorted(left.keys() - right.keys())
    extra = sorted(right.keys() - left.keys())
    changed = sorted(key for key in left.keys() & right.keys() if left[key] != right[key])
    return {
        "matched": not (missing or extra or changed),
        "source": summarize(source),
        "target": summarize(target),
        "differences": {
            "missing_count": len(missing),
            "extra_count": len(extra),
            "changed_count": len(changed),
            "missing_sample": missing[:20],
            "extra_sample": extra[:20],
            "changed_sample": changed[:20],
        },
    }


def reconcile(target: SqlTarget, api: ApiClient, *, cutover: bool = False) -> dict:
    """Optionally require a frozen source fully caught up before approving a read cutover."""
    with target.lock():
        identity, checkpoint = target.checkpoint()
        boundary = api.boundary()
        if identity != boundary.source_id or checkpoint > boundary.high:
            raise CheckpointConflict("Target has no matching published source boundary")
        report = compare(api.snapshot(identity, checkpoint), target.orders())
        report.update(
            {
                "source_id": identity,
                "checkpoint": checkpoint,
                "source_head": boundary.high,
                "source_frozen": boundary.frozen,
                "lag_events": boundary.high - checkpoint,
            }
        )
        report["ready_for_cutover"] = (
            report["matched"] and boundary.frozen and checkpoint == boundary.high
        )
        report["passed"] = report["ready_for_cutover"] if cutover else report["matched"]
        return report
