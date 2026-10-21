"""Environment settings and safe SQL connection-string construction."""

import os
import re
from dataclasses import dataclass, field


def _brace(value: str) -> str:
    """Escape values using SQL connection-string brace escaping."""
    return "{" + value.replace("}", "}}") + "}"


@dataclass(frozen=True)
class Settings:
    """Runtime configuration; secrets are excluded from object representations."""

    api_url: str = "http://localhost:8000"
    api_key: str = field(default="", repr=False)
    sql_server: str = "localhost,1433"
    sql_database: str = "orders_warehouse"
    sql_user: str = "orders_app"
    sql_password: str = field(default="", repr=False)
    sql_admin_password: str = field(default="", repr=False)
    sql_trust_certificate: bool = False
    page_size: int = 500
    max_events: int = 100_000
    attempts: int = 4
    source_db: str = "data/source.db"

    @classmethod
    def from_env(cls) -> "Settings":
        """Read a single, documented configuration surface for all commands."""
        settings = cls(
            api_url=os.getenv("API_URL", "http://localhost:8000").rstrip("/"),
            api_key=os.getenv("API_KEY", ""),
            sql_server=os.getenv("SQL_SERVER", "localhost,1433"),
            sql_database=os.getenv("SQL_DATABASE", "orders_warehouse"),
            sql_user=os.getenv("SQL_USER", "orders_app"),
            sql_password=os.getenv("SQL_PASSWORD", ""),
            sql_admin_password=os.getenv("SQL_ADMIN_PASSWORD", ""),
            sql_trust_certificate=os.getenv("SQL_TRUST_CERTIFICATE", "false").lower() == "true",
            page_size=int(os.getenv("PAGE_SIZE", "500")),
            max_events=int(os.getenv("MAX_EVENTS", "100000")),
            attempts=int(os.getenv("HTTP_ATTEMPTS", "4")),
            source_db=os.getenv("SOURCE_DB", "data/source.db"),
        )
        for identifier in (settings.sql_database, settings.sql_user):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", identifier):
                raise ValueError("SQL database and user names must be simple identifiers")
        if not 1 <= settings.page_size <= 1000 or settings.max_events < settings.page_size:
            raise ValueError("Invalid page size or maximum event count")
        if not 1 <= settings.attempts <= 10:
            raise ValueError("HTTP_ATTEMPTS must be between 1 and 10")
        return settings

    def connection_string(self, *, admin: bool = False, master: bool = False) -> str:
        """Use TLS; trusting a self-signed certificate is an explicit local setting."""
        values = {
            "Server": self.sql_server,
            "Database": "master" if master else self.sql_database,
            "UID": "sa" if admin else self.sql_user,
            "PWD": self.sql_admin_password if admin else self.sql_password,
        }
        return ";".join(f"{key}={_brace(value)}" for key, value in values.items()) + (
            ";Encrypt=yes;TrustServerCertificate="
            + ("yes" if self.sql_trust_certificate else "no")
            + ";Connection Timeout=10;"
        )
