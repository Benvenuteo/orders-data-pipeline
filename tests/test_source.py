"""Actual API and SQLite behavior, not mocked business logic."""

from concurrent.futures import ThreadPoolExecutor

from orders_pipeline.models import OrderInput, OrderPatch
from orders_pipeline.source import SourceStore


def order_input(order_id):
    return OrderInput.model_validate(
        {
            "order_id": order_id,
            "customer_id": 1,
            "total_gross": "10.00",
            "currency": "PLN",
            "status": "new",
        }
    )


def test_authentication_and_health(http_client):
    assert http_client.get("/health", headers={"X-API-Key": "wrong"}).status_code == 200
    assert http_client.get("/v1/boundary", headers={"X-API-Key": "wrong"}).status_code == 401


def test_seed_is_deterministic_and_cannot_overwrite(store, tmp_path):
    store.seed(12, 42)
    other = SourceStore(str(tmp_path / "other.db"))
    other.seed(12, 42)
    assert store.changes(0, 12, 20)["items"] == other.changes(0, 12, 20)["items"]
    assert store.boundary()["source_id"] != other.boundary()["source_id"]


def test_identity_and_data_survive_restart(store):
    store.seed(4, 42)
    assert SourceStore(store.path).boundary() == store.boundary()


def test_duplicate_order_does_not_append_event(http_client, store):
    store.create(order_input(1))
    assert (
        http_client.post("/v1/orders", json=order_input(1).model_dump(mode="json")).status_code
        == 409
    )
    assert store.boundary()["high"] == 1


def test_as_of_snapshot_and_tombstone_preserve_history(store):
    store.seed(3, 42)
    old = store.snapshot(3, 0, 10)
    store.patch(1, OrderPatch(status="paid"))
    store.patch(2, OrderPatch(is_deleted=True))
    assert store.snapshot(3, 0, 10) == old
    new = store.snapshot(5, 0, 10)["items"]
    assert new[0]["order"]["status"] == "paid"
    assert new[1]["order"]["is_deleted"] is True


def test_fixed_boundary_excludes_concurrent_new_changes(store, api):
    store.seed(3, 42)
    boundary = api.boundary()
    store.create(order_input(4))
    assert len(api.snapshot(boundary.source_id, boundary.high)) == 3


def test_freeze_blocks_writes_and_unfreeze_restores_them(http_client, store):
    store.seed(2, 42)
    assert http_client.post("/admin/freeze", json={"frozen": True}).status_code == 200
    assert http_client.patch("/v1/orders/1", json={"status": "paid"}).status_code == 423
    assert http_client.get("/v1/snapshot", params={"at": 2}).status_code == 200
    http_client.post("/admin/freeze", json={"frozen": False})
    assert http_client.patch("/v1/orders/1", json={"status": "paid"}).status_code == 200


def test_concurrent_writes_have_complete_commit_order(store):
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda value: store.create(order_input(value)), range(1, 21)))
    events = store.changes(0, 20, 100)["items"]
    assert [event["change_id"] for event in events] == list(range(1, 21))
    assert {event["order"]["order_id"] for event in events} == set(range(1, 21))


def test_invalid_patch_does_not_modify_source(http_client, store):
    store.seed(1, 42)
    assert http_client.patch("/v1/orders/1", json={"total_gross": "garbage"}).status_code == 422
    assert store.boundary()["high"] == 1
