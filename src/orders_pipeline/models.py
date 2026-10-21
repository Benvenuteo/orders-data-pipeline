"""Source contracts and business validation shared by the API and pipeline."""

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

PositiveId = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
Position = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
Status = Literal["new", "paid", "shipped", "cancelled"]
Currency = Literal["PLN", "EUR", "USD"]


class Contract(BaseModel):
    """Reject undeclared fields so schema changes become visible."""

    model_config = ConfigDict(extra="forbid")


class OrderInput(Contract):
    """An order has one currency and an exact, nonnegative gross amount."""

    order_id: PositiveId
    customer_id: PositiveId
    total_gross: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    currency: Currency
    status: Status

    @field_validator("total_gross", mode="before")
    @classmethod
    def validate_money(cls, value: object) -> Decimal:
        """Accept explicit two-decimal strings; never round binary floats."""
        if not isinstance(value, str) or not re.fullmatch(r"\d{1,12}\.\d{2}", value):
            raise ValueError("Amount must be a decimal string with exactly two fractional digits")
        return Decimal(value)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: object) -> object:
        """Normalize only the documented whitespace and letter-case differences."""
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> object:
        """Normalize known status spellings without inventing missing values."""
        return value.strip().lower() if isinstance(value, str) else value


class Order(OrderInput):
    """A complete source order, including its deletion tombstone."""

    updated_at: datetime
    is_deleted: StrictBool = False

    @field_validator("updated_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        """Preserve the instant and reject timestamps without a time zone."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp must include a time zone")
        return value.astimezone(UTC)


class OrderPatch(Contract):
    """Fields that can be changed through the demonstration source API."""

    status: Status | None = None
    total_gross: str | None = None
    is_deleted: StrictBool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "OrderPatch":
        """An empty patch is an error rather than an unnecessary source event."""
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("Supply at least one non-null change")
        return self


class Change(Contract):
    """Immutable source event; its ID represents commit order, not business time."""

    change_id: PositiveId
    order: Order


class Boundary(Contract):
    """Stable upper bound for a run; source identity survives application restarts."""

    source_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    high: Position
    frozen: StrictBool


class ChangePage(Contract):
    """Transport envelope; validate business records after durable raw storage."""

    source_id: str
    until: Position
    items: list[dict]
    next_after: Position
    has_more: StrictBool


class SnapshotPage(Contract):
    """An as-of view reconstructed from immutable events."""

    source_id: str
    at: Position
    items: list[Change]
    next_order_id: Position
    has_more: StrictBool
