"""Real SQL Server acceptance tests: no SQLite substitute for target transactions."""

from dataclasses import replace
from uuid import uuid4

import pytest

from orders_pipeline.cli import demo
from orders_pipeline.db import SqlTarget, connect
from orders_pipeline.errors import (
    CheckpointConflict,
    ConcurrentRun,
    SimulatedFailure,
    SourceUnavailable,
    ValidationRejected,
)
from orders_pipeline.models import OrderInput, OrderPatch
from orders_pipeline.reconcile import reconcile
from orders_pipeline.runner import Runner

pytestmark = pytest.mark.integration


def test_full_load_noop_updates_and_tombstones(settings, target, store, api):
    store.seed(6, 42)
    first = Runner(settings, target, api).run()
    assert first["inserted"] == 6 and first["checkpoint"] == 6
    noop = Runner(settings, target, api).run()
    assert noop["inserted"] == noop["updated"] == 0
    store.patch(1, OrderPatch(status="paid", total_gross="0.00"))
    store.patch(2, OrderPatch(is_deleted=True))
    store.create(
        OrderInput.model_validate(
            {
                "order_id": 7,
                "customer_id": 1,
                "total_gross": "999999999999.99",
                "currency": "EUR",
                "status": "paid",
            }
        )
    )
    result = Runner(settings, target, api).run()
    assert result["inserted"] == 1 and result["updated"] == 2
    report = reconcile(target, api)
    assert report["passed"]
    assert report["target"]["total"] == 7 and report["target"]["active"] == 6


def test_failed_commit_leaves_old_state_and_offline_replay_recovers(settings, target, store, api):
    store.seed(3, 42)
    Runner(settings, target, api).run()
    before = target.orders()
    store.patch(1, OrderPatch(total_gross="777.77"))
    with pytest.raises(SimulatedFailure):
        Runner(settings, target, api).run(fail_before_commit=True)
    failed = target.status()["last_run"]["run_id"]
    assert target.orders() == before
    assert target.checkpoint()[1] == 3
    # No client exists during replay, so any accidental network dependency fails this test.
    result = Runner(settings, target, None).run(parent=failed)
    assert result["checkpoint"] == 4 and result["updated"] == 1
    assert reconcile(target, api)["passed"]


def test_rejected_business_record_preserves_raw_and_checkpoint(settings, target, store, api):
    store.seed(3, 42)
    store.configure_fault("invalid_record", 1, 0)
    with pytest.raises(ValidationRejected):
        Runner(settings, target, api).run()
    failed = target.status()["last_run"]["run_id"]
    assert target.orders() == [] and target.checkpoint() == (None, 0)
    assert len(target.raw_pages(failed)) == 2
    assert target.execute("SELECT COUNT(*) FROM quality.rejected_records").fetchone()[0] == 1
    with pytest.raises(ValidationRejected):
        Runner(settings, target, None).run(parent=failed)
    # A fresh read repairs the injected transport fault; the source history stayed intact.
    Runner(settings, target, api).run()
    assert reconcile(target, api)["passed"]


def test_http_failure_cannot_be_replayed_as_complete(settings, target, store, api):
    store.seed(3, 42)
    store.configure_fault("http_503", 10, 0)
    with pytest.raises(SourceUnavailable):
        Runner(settings, target, api).run()
    failed = target.status()["last_run"]["run_id"]
    with pytest.raises(CheckpointConflict):
        Runner(settings, target, None).run(parent=failed)
    assert target.checkpoint() == (None, 0)


def test_source_identity_mismatch_cannot_overwrite_existing_data(settings, target, store, api):
    store.seed(2, 42)
    Runner(settings, target, api).run()
    before = target.orders()
    with store.connection(write=True) as connection:
        connection.execute("UPDATE metadata SET source_id=?", (str(uuid4()),))
    with pytest.raises(CheckpointConflict):
        Runner(settings, target, api).run()
    assert target.orders() == before


def test_application_lock_excludes_second_connection(settings, target):
    other = SqlTarget(settings)
    try:
        with target.lock():
            with pytest.raises(ConcurrentRun), other.lock():
                pytest.fail("Second session acquired an exclusive application lock")
    finally:
        other.close()


def test_cutover_requires_freeze_and_zero_lag(settings, target, store, api):
    store.seed(3, 42)
    Runner(settings, target, api).run()
    assert not reconcile(target, api, cutover=True)["passed"]
    store.patch(1, OrderPatch(status="paid"))
    store.freeze(True)
    # As-of equality can pass while source changes are still pending.
    assert reconcile(target, api)["passed"]
    assert not reconcile(target, api, cutover=True)["passed"]
    Runner(settings, target, api).run()
    assert reconcile(target, api, cutover=True)["passed"]


def test_outdated_replay_is_rejected(settings, target, store, api):
    store.seed(2, 42)
    with pytest.raises(SimulatedFailure):
        Runner(settings, target, api).run(fail_before_commit=True)
    failed = target.status()["last_run"]["run_id"]
    Runner(settings, target, api).run()
    with pytest.raises(CheckpointConflict):
        Runner(settings, target, None).run(parent=failed)


def test_sql_login_cannot_create_tables_or_delete_core(settings, target):
    import mssql_python

    with pytest.raises(mssql_python.Error):
        target.execute("CREATE TABLE dbo.forbidden(id int)")
    target.connection.rollback()
    with pytest.raises(mssql_python.Error):
        target.execute("DELETE FROM core.orders")
    target.connection.rollback()


def test_unfinished_run_is_marked_interrupted(settings, target, store, api):
    abandoned = str(uuid4())
    target.start_run(abandoned, 0, None)
    Runner(settings, target, api).run()
    row = target.execute(
        "SELECT status,error_code FROM control.pipeline_runs WHERE run_id=?", (abandoned,)
    ).fetchone()
    assert tuple(row) == ("failed", "Interrupted")


def test_reconciliation_detects_tampered_target(settings, target, store, api):
    store.seed(2, 42)
    Runner(settings, target, api).run()
    admin = connect(settings, admin=True)
    try:
        admin.cursor().execute("UPDATE core.orders SET total_gross=total_gross+1 WHERE order_id=1")
        admin.commit()
    finally:
        admin.close()
    result = reconcile(target, api)
    assert not result["passed"] and result["differences"]["changed_sample"] == [1]


def test_entire_demo_in_real_sql(settings, target, api):
    result = demo(replace(settings, page_size=100), target, api, count=1000)
    assert result["cutover_check"]["ready_for_cutover"]
    assert result["cutover_check"]["target"]["total"] == 1010
    assert result["cutover_check"]["target"]["active"] == 1008
