"""Isolated synthetic sources; SQL integration uses a dedicated test database."""

import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from orders_pipeline.api import create_app
from orders_pipeline.client import ApiClient
from orders_pipeline.settings import Settings


@pytest.fixture
def settings(tmp_path):
    """Use real environment SQL settings but never share a SQLite source between tests."""
    return replace(
        Settings.from_env(),
        api_key="test-key-with-enough-entropy",
        source_db=str(tmp_path / "shop.db"),
        page_size=2,
        attempts=3,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def http_client(app, settings):
    with TestClient(app, headers={"X-API-Key": settings.api_key}) as client:
        yield client


@pytest.fixture
def api(settings, http_client):
    return ApiClient(settings, client=http_client, sleep=lambda seconds: None)


@pytest.fixture
def store(app):
    return app.state.store


@pytest.fixture
def target(settings):
    """Reset only an explicitly enabled *_test database using administrator credentials."""
    if os.getenv("RUN_SQL_TESTS") != "1":
        pytest.skip("Set RUN_SQL_TESTS=1 for real SQL Server integration tests")
    if not settings.sql_database.endswith("_test"):
        pytest.fail("SQL integration tests require a database ending in _test")
    from orders_pipeline.db import SqlTarget, connect, provision

    provision(settings)
    admin = connect(settings, admin=True)
    try:
        cursor = admin.cursor()
        for table in [
            "raw.api_responses",
            "quality.rejected_records",
            "staging.orders",
            "core.orders",
        ]:
            cursor.execute(f"DELETE FROM {table}")
        cursor.execute("UPDATE control.pipeline_runs SET parent_run_id=NULL")
        cursor.execute("DELETE FROM control.pipeline_runs")
        cursor.execute("UPDATE control.pipeline_state SET source_id=NULL,last_change_id=0")
        admin.commit()
    finally:
        admin.close()
    instance = SqlTarget(settings)
    try:
        yield instance
    finally:
        instance.close()
