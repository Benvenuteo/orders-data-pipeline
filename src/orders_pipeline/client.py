"""Bounded HTTP reads with retries only for transient failures."""

import json
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from pydantic import ValidationError

from orders_pipeline.errors import SourceProtocolError, SourceUnavailable
from orders_pipeline.models import Boundary, ChangePage, SnapshotPage
from orders_pipeline.settings import Settings


def parse_json(text: str) -> object:
    """Reject nonstandard JSON numbers rather than allowing NaN into raw processing."""

    def reject_constant(value: str) -> None:
        raise ValueError("Non-finite JSON number")

    try:
        return json.loads(text, parse_constant=reject_constant)
    except (ValueError, TypeError):
        raise SourceProtocolError("Source returned malformed JSON") from None


def validate_page(
    payload: str, *, source_id: str, after: int, until: int, page_size: int
) -> ChangePage:
    """Ensure keyset pagination cannot skip, loop or cross the run's fixed boundary."""
    try:
        page = ChangePage.model_validate(parse_json(payload))
    except ValidationError:
        raise SourceProtocolError("Invalid change-page envelope") from None
    if page.source_id != source_id or page.until != until or len(page.items) > page_size:
        raise SourceProtocolError("Source identity, boundary or page size changed")
    previous = after
    for item in page.items:
        change_id = item.get("change_id")
        if type(change_id) is not int or not previous < change_id <= until:
            raise SourceProtocolError("Change IDs are unordered, repeated or outside the interval")
        previous = change_id
    if page.next_after != previous:
        raise SourceProtocolError("Pagination cursor does not match the returned changes")
    if page.has_more != (previous < until) or (page.has_more and not page.items):
        raise SourceProtocolError("Change feed ended early or cannot make progress")
    return page


class ApiClient:
    """Share one connection pool; mutation requests are intentionally never auto-retried."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if len(settings.api_key) < 16:
            raise ValueError("API_KEY must contain at least 16 characters")
        self.settings = settings
        self.sleep = sleep
        self.client = client or httpx.Client(
            base_url=settings.api_url,
            headers={"X-API-Key": settings.api_key},
            timeout=httpx.Timeout(15, connect=5),
            follow_redirects=False,
        )

    def close(self) -> None:
        """Release HTTP connections explicitly."""
        self.client.close()

    @staticmethod
    def _retry_after(value: str | None) -> float:
        if value is None:
            return 0
        try:
            seconds = float(value)
        except ValueError:
            try:
                instant = parsedate_to_datetime(value)
                seconds = (instant - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return 0
        if seconds > 60:
            raise SourceUnavailable("Retry-After exceeds this job's retry budget; retry later")
        return max(0, seconds)

    def get(self, path: str, params: dict | None = None) -> str:
        """Use finite exponential backoff and honor bounded Retry-After responses."""
        for attempt in range(self.settings.attempts):
            delay = 0.0
            try:
                response = self.client.get(path, params=params)
                if response.status_code == 200:
                    return response.text
                if response.status_code not in {429, 500, 502, 503, 504}:
                    raise SourceProtocolError(
                        f"Source rejected request: HTTP {response.status_code}"
                    )
                delay = self._retry_after(response.headers.get("Retry-After"))
            except httpx.TransportError:
                pass
            if attempt + 1 < self.settings.attempts:
                self.sleep(max(delay, min(2**attempt, 8) + random.uniform(0, 0.2)))
        raise SourceUnavailable("Source unavailable after the configured retry attempts")

    def boundary(self) -> Boundary:
        """Capture a committed source identity and upper change ID."""
        try:
            return Boundary.model_validate(parse_json(self.get("/v1/boundary")))
        except ValidationError:
            raise SourceProtocolError("Invalid source boundary") from None

    def snapshot(self, source_id: str, at: int) -> list:
        """Read an immutable as-of state for exact reconciliation."""
        result = []
        after = 0
        while True:
            try:
                page = SnapshotPage.model_validate(
                    parse_json(
                        self.get(
                            "/v1/snapshot",
                            {
                                "at": at,
                                "after_order_id": after,
                                "limit": self.settings.page_size,
                            },
                        )
                    )
                )
            except ValidationError:
                raise SourceProtocolError("Invalid source snapshot") from None
            if page.source_id != source_id or page.at != at:
                raise SourceProtocolError("Snapshot identity or boundary changed")
            previous = after
            for item in page.items:
                if not previous < item.order.order_id or item.change_id > at:
                    raise SourceProtocolError("Snapshot is unordered or newer than its boundary")
                previous = item.order.order_id
            if page.next_order_id != previous or (page.has_more and not page.items):
                raise SourceProtocolError("Snapshot cursor cannot make progress")
            result.extend(page.items)
            if len(result) > self.settings.max_events:
                raise SourceProtocolError("Snapshot exceeds MAX_EVENTS")
            if not page.has_more:
                return result
            after = previous

    def mutate(self, method: str, path: str, payload: dict) -> dict:
        """Perform a single explicit lab mutation; uncertain writes are never duplicated."""
        try:
            response = self.client.request(method, path, json=payload)
        except httpx.TransportError:
            raise SourceUnavailable(
                "Mutation outcome is uncertain; inspect source before retrying"
            ) from None
        if not 200 <= response.status_code < 300:
            raise SourceProtocolError(f"Source mutation rejected: HTTP {response.status_code}")
        data = parse_json(response.text)
        if not isinstance(data, dict):
            raise SourceProtocolError("Invalid source mutation response")
        return data
