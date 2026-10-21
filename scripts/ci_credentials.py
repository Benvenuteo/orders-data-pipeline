"""Generate per-run SQL/API credentials and mask them before exporting to Actions."""

import os
import secrets
from pathlib import Path


def main() -> None:
    """Use runner environment files; never store credentials in repository artifacts."""
    admin_password = "Admin1!" + secrets.token_hex(24)
    values = {
        "SQL_ADMIN_PASSWORD": admin_password,
        "MSSQL_SA_PASSWORD": admin_password,
        "SQL_PASSWORD": "App1!" + secrets.token_hex(24),
        "API_KEY": secrets.token_hex(24),
    }
    path = Path(os.environ["GITHUB_ENV"])
    with path.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            print(f"::add-mask::{value}")
            stream.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
