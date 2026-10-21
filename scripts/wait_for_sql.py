"""Wait for the ephemeral CI SQL Server without emitting driver connection details."""

import time

from orders_pipeline.db import connect
from orders_pipeline.settings import Settings


def main() -> None:
    """Bound startup retries; fail the workflow if SQL Server never becomes ready."""
    settings = Settings.from_env()
    for _ in range(30):
        connection = None
        try:
            connection = connect(settings, admin=True, master=True, autocommit=True)
            connection.cursor().execute("SELECT 1").fetchone()
            print("SQL Server is ready.")
            return
        except Exception:
            time.sleep(2)
        finally:
            if connection:
                connection.close()
    raise SystemExit("SQL Server did not become ready within the startup retry budget")


if __name__ == "__main__":
    main()
