"""SQL Server persistence, provisioning and atomic publication."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from typing import Any

from orders_pipeline.errors import CheckpointConflict, ConcurrentRun, SimulatedFailure
from orders_pipeline.models import Change
from orders_pipeline.settings import Settings


def connect(
    settings: Settings, *, admin: bool = False, master: bool = False, autocommit: bool = False
) -> Any:
    """Import the native driver only for commands that actually use SQL Server."""
    import mssql_python

    return mssql_python.connect(
        settings.connection_string(admin=admin, master=master), autocommit=autocommit, timeout=10
    )


def provision(settings: Settings) -> None:
    """Create the local lab database and restricted login using administrator credentials."""
    if not settings.sql_admin_password or not settings.sql_password:
        raise ValueError("SQL_ADMIN_PASSWORD and SQL_PASSWORD are required for initialization")
    connection = connect(settings, admin=True, master=True, autocommit=True)
    try:
        cursor = connection.cursor()
        exists = cursor.execute(
            "SELECT 1 FROM sys.databases WHERE name=?", (settings.sql_database,)
        ).fetchone()
        if not exists:
            cursor.execute(f"CREATE DATABASE [{settings.sql_database}]")
        exists = cursor.execute(
            "SELECT 1 FROM sys.sql_logins WHERE name=?", (settings.sql_user,)
        ).fetchone()
        if not exists:
            # Identifiers are validated; SQL string literals must escape single quotes.
            password = settings.sql_password.replace("'", "''")
            cursor.execute(
                f"CREATE LOGIN [{settings.sql_user}] WITH PASSWORD=N'{password}', CHECK_POLICY=ON"
            )
    finally:
        connection.close()
    connection = connect(settings, admin=True)
    try:
        cursor = connection.cursor()
        cursor.execute("SET XACT_ABORT ON")
        exists = cursor.execute("SELECT OBJECT_ID(N'control.schema_versions')").fetchone()[0]
        if exists:
            versions = [
                row[0]
                for row in cursor.execute(
                    "SELECT version FROM control.schema_versions ORDER BY version"
                ).fetchall()
            ]
            if versions != [1]:
                raise ValueError("Unsupported schema version; apply reviewed migrations")
        else:
            script = files("orders_pipeline").joinpath("sql/001_initial.sql").read_text()
            for batch in re.split(r"^GO\s*$", script, flags=re.MULTILINE):
                if batch.strip():
                    cursor.execute(batch)
        cursor.execute(f"""
            IF DATABASE_PRINCIPAL_ID(N'{settings.sql_user}') IS NULL
                CREATE USER [{settings.sql_user}] FOR LOGIN [{settings.sql_user}];
            GRANT SELECT,INSERT,UPDATE,DELETE ON SCHEMA::raw TO [{settings.sql_user}];
            GRANT SELECT,INSERT,UPDATE,DELETE ON SCHEMA::staging TO [{settings.sql_user}];
            GRANT SELECT,INSERT,UPDATE ON SCHEMA::control TO [{settings.sql_user}];
            GRANT SELECT,INSERT,UPDATE ON SCHEMA::quality TO [{settings.sql_user}];
            GRANT SELECT,INSERT,UPDATE ON SCHEMA::core TO [{settings.sql_user}];
            GRANT SELECT ON SCHEMA::reporting TO [{settings.sql_user}];
        """)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


class SqlTarget:
    """One SQL connection owns a session lock throughout extraction and publication."""

    def __init__(self, settings: Settings) -> None:
        self.connection = connect(settings)

    def close(self) -> None:
        """Closing the session also releases a lock after unexpected process termination."""
        self.connection.close()

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute a parameterized statement; callers explicitly control transactions."""
        cursor = self.connection.cursor()
        cursor.execute(sql, params)
        return cursor

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Exclude imports, replays and reconciliation across processes and hosts."""
        row = self.execute("""
            SET NOCOUNT ON;
            DECLARE @result int;
            EXEC @result=sys.sp_getapplock @Resource=N'orders-pipeline-v1',
                @LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0;
            SELECT @result;
        """).fetchone()
        self.connection.commit()
        if row[0] < 0:
            raise ConcurrentRun("Another process holds the pipeline lock")
        try:
            # A killed process cannot mark itself failed; lock ownership proves it is gone.
            self.execute("""
                UPDATE control.pipeline_runs SET status='failed',
                    error_code='Interrupted',finished_at=SYSUTCDATETIME()
                WHERE status='running'
            """)
            self.connection.commit()
            yield
        finally:
            self.connection.rollback()
            self.execute("""
                EXEC sys.sp_releaseapplock @Resource=N'orders-pipeline-v1',@LockOwner='Session'
            """)
            self.connection.commit()

    def checkpoint(self) -> tuple[str | None, int]:
        """Read the committed source identity and last published change."""
        row = self.execute(
            "SELECT source_id,last_change_id FROM control.pipeline_state WHERE singleton=1"
        ).fetchone()
        return row[0], int(row[1])

    def start_run(self, run_id: str, lower: int, parent: str | None) -> None:
        """Persist a run before contacting the source."""
        self.execute(
            """
            INSERT INTO control.pipeline_runs(run_id,parent_run_id,kind,status,lower_id)
            VALUES(?,?,?,'running',?)
        """,
            (run_id, parent, "replay" if parent else "import", lower),
        )
        self.connection.commit()

    def set_boundary(self, run_id: str, source_id: str, high: int) -> None:
        """Keep the requested interval even if extraction subsequently fails."""
        self.execute(
            "UPDATE control.pipeline_runs SET source_id=?,upper_id=? WHERE run_id=?",
            (source_id, high, run_id),
        )
        self.connection.commit()

    def save_raw(self, run_id: str, page_no: int, after: int, payload: str) -> None:
        """Durably store the exact response text before parsing it."""
        self.execute(
            "INSERT INTO raw.api_responses(run_id,page_no,after_id,payload) VALUES(?,?,?,?)",
            (run_id, page_no, after, payload),
        )
        self.connection.commit()

    def raw_pages(self, run_id: str) -> list[tuple[int, str]]:
        """Return raw pages in extraction order for validation or offline replay."""
        return [
            (int(row[0]), row[1])
            for row in self.execute(
                "SELECT after_id,payload FROM raw.api_responses WHERE run_id=? ORDER BY page_no",
                (run_id,),
            ).fetchall()
        ]

    def extracted(self, run_id: str, fetched: int) -> None:
        """Only complete extraction can later be replayed without the source API."""
        self.execute(
            """
            UPDATE control.pipeline_runs SET extraction_complete=1,fetched=? WHERE run_id=?
        """,
            (fetched, run_id),
        )
        self.connection.commit()

    def replay_interval(self, parent: str, lower: int) -> tuple[str, int]:
        """Reject incomplete, successful or outdated runs instead of skipping newer data."""
        row = self.execute(
            """
            SELECT source_id,upper_id,lower_id,status,extraction_complete
            FROM control.pipeline_runs WHERE run_id=?
        """,
            (parent,),
        ).fetchone()
        if not row or row[3] != "failed" or not row[4] or row[2] != lower:
            raise CheckpointConflict(
                "Replay requires a complete failed run at the current checkpoint"
            )
        return row[0], int(row[1])

    def save_rejections(self, run_id: str, rejected: list[tuple]) -> None:
        """Preserve the payload and safe validation reasons without publishing partial data."""
        if rejected:
            cursor = self.connection.cursor()
            cursor.executemany(
                """
                INSERT INTO quality.rejected_records(run_id,record_no,change_id,payload,reasons)
                VALUES(?,?,?,?,?)
            """,
                [(run_id, *item) for item in rejected],
            )
            self.execute(
                "UPDATE control.pipeline_runs SET rejected=? WHERE run_id=?",
                (len(rejected), run_id),
            )
            self.connection.commit()

    def stage(self, run_id: str, changes: list[Change]) -> None:
        """Stage only the final after-image per order within a bounded run."""
        rows = []
        for change in changes:
            order = change.order
            rows.append(
                (
                    run_id,
                    order.order_id,
                    order.customer_id,
                    order.total_gross,
                    order.currency,
                    order.status,
                    order.updated_at.replace(tzinfo=None),
                    order.is_deleted,
                    change.change_id,
                )
            )
        if rows:
            self.connection.cursor().executemany(
                "INSERT INTO staging.orders VALUES(?,?,?,?,?,?,?,?,?)", rows
            )
        self.connection.commit()

    def publish(
        self,
        run_id: str,
        source_id: str,
        lower: int,
        high: int,
        *,
        fail_before_commit: bool = False,
    ) -> dict:
        """Publish orders, checkpoint and success state in exactly one transaction."""
        try:
            self.execute("SET XACT_ABORT ON")
            row = self.execute("""
                SELECT source_id,last_change_id FROM control.pipeline_state WITH(UPDLOCK,HOLDLOCK)
                WHERE singleton=1
            """).fetchone()
            if row[1] != lower or row[0] not in (None, source_id):
                raise CheckpointConflict("Checkpoint changed or belongs to another source")
            counts = self.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN t.order_id IS NULL THEN 1 ELSE 0 END),0),
                    COALESCE(SUM(CASE WHEN t.order_id IS NOT NULL
                        AND s.change_id>t.last_change_id THEN 1 ELSE 0 END),0),
                    COALESCE(SUM(CASE WHEN s.is_deleted=1 THEN 1 ELSE 0 END),0)
                FROM staging.orders s LEFT JOIN core.orders t ON t.order_id=s.order_id
                WHERE s.run_id=?
            """,
                (run_id,),
            ).fetchone()
            self.execute(
                """
                UPDATE t SET customer_id=s.customer_id,total_gross=s.total_gross,
                    currency=s.currency,status=s.status,updated_at=s.updated_at,
                    is_deleted=s.is_deleted,last_change_id=s.change_id,last_run_id=s.run_id
                FROM core.orders t JOIN staging.orders s ON s.order_id=t.order_id
                WHERE s.run_id=? AND s.change_id>t.last_change_id
            """,
                (run_id,),
            )
            self.execute(
                """
                INSERT INTO core.orders
                SELECT s.order_id,s.customer_id,s.total_gross,s.currency,s.status,
                    s.updated_at,s.is_deleted,s.change_id,s.run_id
                FROM staging.orders s WHERE s.run_id=? AND NOT EXISTS(
                    SELECT 1 FROM core.orders t WHERE t.order_id=s.order_id)
            """,
                (run_id,),
            )
            if fail_before_commit:
                raise SimulatedFailure("Requested failure immediately before publication commit")
            self.execute(
                """
                UPDATE control.pipeline_state SET source_id=?,last_change_id=? WHERE singleton=1
            """,
                (source_id, high),
            )
            self.execute(
                """
                UPDATE control.pipeline_runs SET status='succeeded',finished_at=SYSUTCDATETIME(),
                    inserted=?,updated=?,tombstones=? WHERE run_id=?
            """,
                (int(counts[0]), int(counts[1]), int(counts[2]), run_id),
            )
            self.connection.commit()
            return {
                "inserted": int(counts[0]),
                "updated": int(counts[1]),
                "tombstones": int(counts[2]),
            }
        except BaseException:
            self.connection.rollback()
            raise

    def fail(self, run_id: str, error_code: str) -> None:
        """Record failure after rollback; raw and staging data remain available."""
        self.connection.rollback()
        self.execute(
            """
            UPDATE control.pipeline_runs SET status='failed',error_code=?,
                finished_at=SYSUTCDATETIME() WHERE run_id=?
        """,
            (error_code[:100], run_id),
        )
        self.connection.commit()

    def orders(self) -> list[Change]:
        """Return canonical target rows for exact as-of reconciliation."""
        rows = self.execute("""
            SELECT order_id,customer_id,total_gross,currency,status,updated_at,
                   is_deleted,last_change_id FROM core.orders ORDER BY order_id
        """).fetchall()
        return [
            Change.model_validate(
                {
                    "change_id": int(row[7]),
                    "order": {
                        "order_id": int(row[0]),
                        "customer_id": int(row[1]),
                        "total_gross": format(row[2], ".2f"),
                        "currency": row[3].strip(),
                        "status": row[4],
                        "updated_at": row[5].isoformat() + "+00:00",
                        "is_deleted": bool(row[6]),
                    },
                }
            )
            for row in rows
        ]

    def status(self) -> dict:
        """Operational status includes freshness even when the scheduler stops running."""
        row = self.execute("""
            SELECT COUNT_BIG(*),COALESCE(SUM(CASE WHEN is_deleted=0 THEN 1 ELSE 0 END),0)
            FROM core.orders
        """).fetchone()
        source_id, high = self.checkpoint()
        last = self.execute("""
            SELECT TOP(1) CONVERT(varchar(36),run_id),status,error_code,finished_at
            FROM control.pipeline_runs ORDER BY started_at DESC
        """).fetchone()
        age = self.execute("""
            SELECT DATEDIFF(second,MAX(finished_at),SYSUTCDATETIME())
            FROM control.pipeline_runs WHERE status='succeeded'
        """).fetchone()[0]
        return {
            "source_id": source_id,
            "checkpoint": high,
            "total_orders": int(row[0]),
            "active_orders": int(row[1]),
            "last_run": {
                "run_id": last[0],
                "status": last[1],
                "error_code": last[2],
                "finished_at": str(last[3]),
            }
            if last
            else None,
            "seconds_since_success": age,
            "healthy": bool(last and last[1] == "succeeded" and age is not None and age <= 1800),
        }
