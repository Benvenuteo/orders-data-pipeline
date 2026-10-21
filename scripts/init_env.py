"""Generate local lab secrets once; never overwrite an existing .env file."""

import secrets
from pathlib import Path


def main() -> None:
    """Create Docker Compose settings without printing passwords to the terminal."""
    root = Path(__file__).resolve().parents[1]
    path = root / ".env"
    data = {
        "API_KEY": secrets.token_hex(24),
        "SQL_ADMIN_PASSWORD": "Admin1!" + secrets.token_hex(18),
        "SQL_PASSWORD": "App1!" + secrets.token_hex(18),
        "SQL_DATABASE": "orders_warehouse",
        "SQL_USER": "orders_app",
        "SQL_TRUST_CERTIFICATE": "true",
        "PAGE_SIZE": "500",
        "MAX_EVENTS": "100000",
        "HTTP_ATTEMPTS": "4",
    }
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(
                "# Local synthetic lab; self-signed SQL certificate. Never commit this file.\n"
            )
            stream.writelines(f"{key}={value}\n" for key, value in data.items())
        path.chmod(0o600)
        print("Created .env with new local secrets.")
    except FileExistsError:
        print("Existing .env preserved.")


if __name__ == "__main__":
    main()
