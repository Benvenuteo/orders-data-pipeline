"""Validate configuration against the installed SQL driver's real parser."""

import secrets

import pytest

from orders_pipeline.settings import Settings


def test_connection_string_is_accepted_by_the_locked_driver():
    from mssql_python.connection import _ConnectionStringParser

    password = secrets.token_hex(12) + ";a}b"
    settings = Settings(sql_password=password, sql_trust_certificate=True)
    parsed = _ConnectionStringParser(validate_keywords=True)._parse(settings.connection_string())
    normalized = _ConnectionStringParser._normalize_params(parsed)
    assert normalized["PWD"] == password
    assert normalized["Database"] == "orders_warehouse"


def test_secrets_do_not_appear_in_settings_repr():
    secret = secrets.token_hex(16)
    settings = Settings(api_key=secret, sql_password=secret, sql_admin_password=secret)
    assert secret not in repr(settings)


@pytest.mark.parametrize("name", ["invalid-name", "db;DROP TABLE x", "[db]", "1db"])
def test_unsafe_sql_identifier_is_rejected(monkeypatch, name):
    monkeypatch.setenv("SQL_DATABASE", name)
    with pytest.raises(ValueError):
        Settings.from_env()
