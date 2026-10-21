"""SQLite demonstration source with an atomic, immutable change feed."""

import json
import random
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from orders_pipeline.models import Order, OrderInput, OrderPatch


class SourceStore:
    """Serialize source writes so change IDs always follow transaction commit order."""

    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    source_id TEXT NOT NULL,
                    frozen INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS changes (
                    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_changes_order ON changes(order_id, change_id);
                CREATE TABLE IF NOT EXISTS orders (
                    order_id INTEGER PRIMARY KEY,
                    change_id INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fault (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    mode TEXT NOT NULL,
                    remaining INTEGER NOT NULL,
                    delay REAL NOT NULL
                );
            """)
            connection.execute(
                "INSERT OR IGNORE INTO metadata(singleton,source_id) VALUES(1,?)", (str(uuid4()),)
            )
            connection.execute("INSERT OR IGNORE INTO fault VALUES(1,'none',0,0)")

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """Commit on success and always close, including after a failed source write."""
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def boundary(self) -> dict:
        """Read source identity, freeze status and a committed high-water mark together."""
        with self.connection() as connection:
            row = connection.execute("""
                SELECT source_id, frozen,
                       (SELECT COALESCE(MAX(change_id),0) FROM changes) AS high
                FROM metadata WHERE singleton=1
            """).fetchone()
            return {
                "source_id": row["source_id"],
                "high": row["high"],
                "frozen": bool(row["frozen"]),
            }

    @staticmethod
    def _ensure_writable(connection: sqlite3.Connection) -> None:
        if connection.execute("SELECT frozen FROM metadata").fetchone()[0]:
            raise PermissionError("Source writes are frozen")

    @staticmethod
    def _append(connection: sqlite3.Connection, order: Order) -> dict:
        """Update current state and append the complete after-image in one transaction."""
        payload = order.model_dump_json()
        cursor = connection.execute(
            "INSERT INTO changes(order_id,payload) VALUES(?,?)", (order.order_id, payload)
        )
        change_id = cursor.lastrowid
        connection.execute(
            """INSERT INTO orders VALUES(?,?,?)
               ON CONFLICT(order_id) DO UPDATE SET
               change_id=excluded.change_id,payload=excluded.payload""",
            (order.order_id, change_id, payload),
        )
        return {"change_id": change_id, "order": order.model_dump(mode="json")}

    def create(self, data: OrderInput) -> dict:
        """Create an order; a duplicate identifier does not append a change."""
        with self.connection(write=True) as connection:
            self._ensure_writable(connection)
            if connection.execute(
                "SELECT 1 FROM orders WHERE order_id=?", (data.order_id,)
            ).fetchone():
                raise FileExistsError("Order already exists")
            order = Order.model_validate(
                {**data.model_dump(mode="json"), "updated_at": datetime.now(UTC)}
            )
            return self._append(connection, order)

    def patch(self, order_id: int, patch: OrderPatch) -> dict:
        """Append updates and deletion tombstones while preserving the order identity."""
        with self.connection(write=True) as connection:
            self._ensure_writable(connection)
            row = connection.execute(
                "SELECT payload FROM orders WHERE order_id=?", (order_id,)
            ).fetchone()
            if row is None:
                raise LookupError("Order does not exist")
            data = json.loads(row[0])
            data.update(patch.model_dump(exclude_unset=True))
            data["updated_at"] = datetime.now(UTC)
            return self._append(connection, Order.model_validate(data))

    def seed(self, count: int, seed: int) -> dict:
        """Build deterministic synthetic business data only in an empty source."""
        generator = random.Random(seed)
        with self.connection(write=True) as connection:
            self._ensure_writable(connection)
            if connection.execute("SELECT 1 FROM changes LIMIT 1").fetchone():
                raise FileExistsError("Seeding requires an empty source")
            for order_id in range(1, count + 1):
                cents = generator.randint(100, 500_000)
                order = Order.model_validate(
                    {
                        "order_id": order_id,
                        "customer_id": generator.randint(1, max(count // 3, 1)),
                        "total_gross": f"{cents // 100}.{cents % 100:02}",
                        "currency": generator.choice(["PLN", "EUR", "USD"]),
                        "status": generator.choice(["new", "paid", "shipped"]),
                        "updated_at": datetime(2026, 1, 1, tzinfo=UTC)
                        + timedelta(seconds=order_id),
                    }
                )
                self._append(connection, order)
        return {"seeded": count, **self.boundary()}

    def changes(self, after: int, until: int, limit: int) -> dict:
        """Keyset pagination over an immutable, bounded change interval."""
        boundary = self.boundary()
        if not after <= until <= boundary["high"]:
            raise ValueError("Requested interval does not exist")
        with self.connection() as connection:
            rows = connection.execute(
                """SELECT change_id,payload FROM changes
                   WHERE change_id>? AND change_id<=? ORDER BY change_id LIMIT ?""",
                (after, until, limit + 1),
            ).fetchall()
        items = [{"change_id": row[0], "order": json.loads(row[1])} for row in rows[:limit]]
        return {
            "source_id": boundary["source_id"],
            "until": until,
            "items": items,
            "next_after": items[-1]["change_id"] if items else after,
            "has_more": len(rows) > limit,
        }

    def snapshot(self, at: int, after_order_id: int, limit: int) -> dict:
        """Reconstruct state at a boundary, including deletion tombstones."""
        boundary = self.boundary()
        if at > boundary["high"]:
            raise ValueError("Snapshot boundary is in the future")
        with self.connection() as connection:
            rows = connection.execute(
                """
                WITH ranked AS (
                    SELECT order_id,change_id,payload,
                           ROW_NUMBER() OVER(PARTITION BY order_id ORDER BY change_id DESC) AS rn
                    FROM changes WHERE change_id<=? AND order_id>?
                )
                SELECT order_id,change_id,payload FROM ranked
                WHERE rn=1 ORDER BY order_id LIMIT ?
            """,
                (at, after_order_id, limit + 1),
            ).fetchall()
        return {
            "source_id": boundary["source_id"],
            "at": at,
            "items": [{"change_id": row[1], "order": json.loads(row[2])} for row in rows[:limit]],
            "next_order_id": rows[min(len(rows), limit) - 1][0] if rows else after_order_id,
            "has_more": len(rows) > limit,
        }

    def freeze(self, frozen: bool) -> dict:
        """Freeze only business writes; reads and operational controls remain available."""
        with self.connection(write=True) as connection:
            connection.execute("UPDATE metadata SET frozen=?", (int(frozen),))
        return self.boundary()

    def configure_fault(self, mode: str, remaining: int, delay: float) -> None:
        """Persist a finite failure scenario for demonstration and automated tests."""
        with self.connection(write=True) as connection:
            connection.execute(
                "UPDATE fault SET mode=?,remaining=?,delay=?", (mode, remaining, delay)
            )

    def consume_fault(self) -> tuple[str, float]:
        """Consume one fault atomically; normal requests resume after the configured count."""
        with self.connection(write=True) as connection:
            row = connection.execute("SELECT mode,remaining,delay FROM fault").fetchone()
            if row[1] <= 0:
                return "none", 0
            connection.execute("UPDATE fault SET remaining=remaining-1")
            return row[0], row[2]
