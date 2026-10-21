"""Extraction, strict validation, offline replay and atomic publication."""

import json
import logging
from uuid import uuid4

from pydantic import ValidationError

from orders_pipeline.client import ApiClient, validate_page
from orders_pipeline.db import SqlTarget
from orders_pipeline.errors import CheckpointConflict, SourceProtocolError, ValidationRejected
from orders_pipeline.models import Change
from orders_pipeline.settings import Settings

LOGGER = logging.getLogger(__name__)


class Runner:
    """Orchestrate a bounded run; publication is the only operation changing core data."""

    def __init__(self, settings: Settings, target: SqlTarget, api: ApiClient | None) -> None:
        self.settings = settings
        self.target = target
        self.api = api

    def run(self, *, parent: str | None = None, fail_before_commit: bool = False) -> dict:
        """Import a source interval or replay a complete failed interval without HTTP."""
        with self.target.lock():
            source_id, lower = self.target.checkpoint()
            run_id = str(uuid4())
            self.target.start_run(run_id, lower, parent)
            LOGGER.info("run_started", extra={"run_id": run_id})
            try:
                if parent:
                    identity, high = self.target.replay_interval(parent, lower)
                else:
                    if self.api is None:
                        raise ValueError("An API client is required for imports")
                    boundary = self.api.boundary()
                    identity, high = boundary.source_id, boundary.high
                if source_id not in (None, identity) or high < lower:
                    raise CheckpointConflict("Source reset or identity mismatch; use a new target")
                self.target.set_boundary(run_id, identity, high)
                if parent:
                    for page_no, (after, payload) in enumerate(self.target.raw_pages(parent), 1):
                        self.target.save_raw(run_id, page_no, after, payload)
                else:
                    self._extract(run_id, identity, lower, high)
                changes = self._validate(run_id, identity, lower, high)
                self.target.stage(run_id, changes)
                counts = self.target.publish(
                    run_id, identity, lower, high, fail_before_commit=fail_before_commit
                )
                result = {
                    "run_id": run_id,
                    "status": "succeeded",
                    "source_id": identity,
                    "lower": lower,
                    "checkpoint": high,
                    **counts,
                }
                LOGGER.info("run_succeeded", extra=result)
                return result
            except BaseException as exc:
                # Driver exceptions can contain connection details: persist only their type.
                self.target.fail(run_id, type(exc).__name__)
                LOGGER.error(
                    "run_failed", extra={"run_id": run_id, "error_code": type(exc).__name__}
                )
                raise

    def _extract(self, run_id: str, source_id: str, lower: int, high: int) -> None:
        after, page_no, fetched = lower, 0, 0
        while after < high:
            page_no += 1
            payload = self.api.get(
                "/v1/changes",
                {
                    "after": after,
                    "until": high,
                    "limit": self.settings.page_size,
                },
            )
            self.target.save_raw(run_id, page_no, after, payload)
            page = validate_page(
                payload,
                source_id=source_id,
                after=after,
                until=high,
                page_size=self.settings.page_size,
            )
            fetched += len(page.items)
            if fetched > self.settings.max_events:
                raise SourceProtocolError(
                    "Interval exceeds MAX_EVENTS; increase the reviewed limit"
                )
            after = page.next_after

    def _validate(self, run_id: str, source_id: str, lower: int, high: int) -> list[Change]:
        after, record_no = lower, 0
        latest: dict[int, Change] = {}
        rejected = []
        for stored_after, payload in self.target.raw_pages(run_id):
            if stored_after != after:
                raise SourceProtocolError("Raw pages are incomplete or unordered")
            page = validate_page(
                payload,
                source_id=source_id,
                after=after,
                until=high,
                page_size=self.settings.page_size,
            )
            for item in page.items:
                record_no += 1
                if record_no > self.settings.max_events:
                    raise SourceProtocolError("Raw interval exceeds MAX_EVENTS")
                try:
                    change = Change.model_validate(item)
                    latest[change.order.order_id] = change
                except ValidationError as exc:
                    # Field locations and error codes are sufficient; avoid logging payloads.
                    reasons = [
                        {"field": list(error["loc"]), "type": error["type"]}
                        for error in exc.errors(include_input=False, include_url=False)
                    ]
                    rejected.append(
                        (
                            record_no,
                            item.get("change_id"),
                            json.dumps(item, ensure_ascii=False),
                            json.dumps(reasons),
                        )
                    )
            after = page.next_after
        if after != high:
            raise SourceProtocolError("Raw data does not cover the complete requested interval")
        self.target.extracted(run_id, record_no)
        self.target.save_rejections(run_id, rejected)
        if rejected:
            raise ValidationRejected(f"{len(rejected)} record(s) rejected; publication blocked")
        return list(latest.values())
