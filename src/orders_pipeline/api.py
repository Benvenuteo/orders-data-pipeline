"""Authenticated synthetic shop API, including controlled fault injection."""

import hmac
import time
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import Field, ValidationError

from orders_pipeline.models import Contract, OrderInput, OrderPatch
from orders_pipeline.settings import Settings
from orders_pipeline.source import SourceStore


class SeedRequest(Contract):
    """A bounded, deterministic source initialization request."""

    count: int = Field(default=10_000, ge=1, le=100_000)
    seed: int = 42


class FreezeRequest(Contract):
    """Explicit migration write-freeze control."""

    frozen: bool


class FaultRequest(Contract):
    """Finite operational faults; they never modify the source change log."""

    mode: Literal["none", "http_429", "http_503", "invalid_record", "delay"]
    remaining: int = Field(default=1, ge=0, le=100)
    delay: float = Field(default=0.1, ge=0, le=10)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct an isolated app; importing the module does not create source data."""
    settings = settings or Settings.from_env()
    if len(settings.api_key) < 16:
        raise ValueError("API_KEY must contain at least 16 characters")
    store = SourceStore(settings.source_db)
    app = FastAPI(title="Synthetic Orders API", version="1.0.0")
    app.state.store = store

    def authenticate(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if x_api_key is None or not hmac.compare_digest(
            x_api_key.encode(), settings.api_key.encode()
        ):
            raise HTTPException(401, "Invalid API key")

    secured = [Depends(authenticate)]

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/boundary", dependencies=secured)
    def boundary() -> dict:
        return store.boundary()

    @app.get("/v1/changes", dependencies=secured)
    def changes(
        after: Annotated[int, Query(ge=0)],
        until: Annotated[int, Query(ge=0)],
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    ) -> dict:
        mode, delay = store.consume_fault()
        if mode == "http_429":
            raise HTTPException(429, "Simulated rate limit", headers={"Retry-After": "0"})
        if mode == "http_503":
            raise HTTPException(503, "Simulated outage")
        if mode == "delay":
            time.sleep(delay)
        try:
            page = store.changes(after, until, limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        # Deliberately bypass a business response model to exercise consumer validation.
        if mode == "invalid_record" and page["items"]:
            page["items"][0]["order"]["total_gross"] = "invalid"
        return page

    @app.get("/v1/snapshot", dependencies=secured)
    def snapshot(
        at: Annotated[int, Query(ge=0)],
        after_order_id: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    ) -> dict:
        try:
            return store.snapshot(at, after_order_id, limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/v1/orders", dependencies=secured, status_code=201)
    def create(data: OrderInput) -> dict:
        try:
            return store.create(data)
        except PermissionError:
            raise HTTPException(423, "Source writes are frozen") from None
        except FileExistsError:
            raise HTTPException(409, "Order already exists") from None

    @app.patch("/v1/orders/{order_id}", dependencies=secured)
    def patch(order_id: int, data: OrderPatch) -> dict:
        try:
            return store.patch(order_id, data)
        except PermissionError:
            raise HTTPException(423, "Source writes are frozen") from None
        except LookupError:
            raise HTTPException(404, "Order does not exist") from None
        except ValidationError:
            raise HTTPException(422, "Invalid business values") from None

    @app.post("/admin/seed", dependencies=secured)
    def seed(data: SeedRequest) -> dict:
        try:
            return store.seed(data.count, data.seed)
        except PermissionError:
            raise HTTPException(423, "Source writes are frozen") from None
        except FileExistsError:
            raise HTTPException(409, "Seeding requires an empty source") from None

    @app.post("/admin/freeze", dependencies=secured)
    def freeze(data: FreezeRequest) -> dict:
        return store.freeze(data.frozen)

    @app.post("/admin/fault", dependencies=secured)
    def fault(data: FaultRequest) -> dict:
        store.configure_fault(data.mode, data.remaining, data.delay)
        return {"configured": data.mode, "remaining": data.remaining}

    return app
