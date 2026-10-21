"""Retry budgets and hostile or inconsistent pagination contracts."""

import json

import httpx
import pytest

from orders_pipeline.client import ApiClient, parse_json, validate_page
from orders_pipeline.errors import SourceProtocolError, SourceUnavailable


def test_rate_limit_is_retried_then_succeeds(store, api):
    store.seed(2, 42)
    store.configure_fault("http_429", 2, 0)
    assert len(parse_json(api.get("/v1/changes", {"after": 0, "until": 2}))["items"]) == 2


def test_transient_failure_has_finite_budget(store, api):
    store.seed(2, 42)
    store.configure_fault("http_503", 10, 0)
    with pytest.raises(SourceUnavailable):
        api.get("/v1/changes", {"after": 0, "until": 2})


def test_authentication_failure_is_not_retried(settings):
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(401)

    with httpx.Client(transport=httpx.MockTransport(transport), base_url="http://test") as http:
        client = ApiClient(settings, client=http, sleep=lambda seconds: None)
        with pytest.raises(SourceProtocolError):
            client.boundary()
    assert len(calls) == 1


def test_timeouts_retry_and_eventually_recover(settings):
    calls = []

    def transport(request):
        calls.append(request)
        if len(calls) < 3:
            raise httpx.ReadTimeout("simulated", request=request)
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(transport), base_url="http://test") as http:
        client = ApiClient(settings, client=http, sleep=lambda seconds: None)
        assert parse_json(client.get("/example")) == {"ok": True}
    assert len(calls) == 3


@pytest.mark.parametrize("text", ["not JSON", '{"amount": NaN}', '{"amount": Infinity}'])
def test_nonstandard_json_is_rejected(text):
    with pytest.raises(SourceProtocolError):
        parse_json(text)


@pytest.mark.parametrize(
    "override",
    [
        {"source_id": "reset"},
        {"until": 8},
        {"next_after": 0},
        {"has_more": False},
        {"items": []},
        {"items": [{"change_id": 1}, {"change_id": 1}]},
        {"items": [{"change_id": True}]},
        {"items": [{"change_id": 9}]},
    ],
)
def test_invalid_pagination_fails_instead_of_silently_skipping(override):
    page = {
        "source_id": "same",
        "until": 3,
        "items": [{"change_id": 1}],
        "next_after": 1,
        "has_more": True,
        **override,
    }
    with pytest.raises(SourceProtocolError):
        validate_page(json.dumps(page), source_id="same", after=0, until=3, page_size=2)


def test_excessive_retry_after_stops_instead_of_retrying_early():
    with pytest.raises(SourceUnavailable):
        ApiClient._retry_after("120")
