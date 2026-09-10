from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api.app as app_module
from api.app import create_app
from graph_core import DatabaseBackupError, DatabaseMigrationError, DatabaseSchemaError, GraphStore
from graph_core.database_lifecycle import inspect_database_file
from graph_core.migrations import V1


def _legacy_v1_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(V1)
        conn.execute(
            "CREATE TABLE schema_migrations("
            "version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_migrations VALUES(1,'legacy')")
        conn.commit()
    finally:
        conn.close()


def test_managed_startup_creates_verified_pre_migration_backup(monkeypatch, tmp_path):
    database = tmp_path / "workspace.db"
    _legacy_v1_database(database)
    monkeypatch.setenv("WEAVEPATH_DB", str(database))

    app = create_app()
    with TestClient(app) as client:
        health = client.get("/api/v1/health").json()
        status = client.get("/api/v1/system/database").json()
        assert health["databaseStatus"] == "migrated"
        assert status["status"] == "migrated"
        assert status["graphVersion"] == 7
        assert status["runtimeVersion"] == 2
        assert status["integrity"] == "ok"
        assert status["downgradeSupported"] is False
        backup = Path(status["backupPath"])
        manifest = Path(status["backupManifestPath"])
        assert backup.parent == database.parent / "backups"
        assert inspect_database_file(backup)["graphVersion"] == 1
        journal = json.loads(manifest.read_text(encoding="utf-8"))
        assert journal["status"] == "completed"
        assert journal["source"]["graphVersion"] == 1
        assert journal["target"] == {"graphVersion": 7, "runtimeVersion": 2}
        assert journal["backupIntegrity"] == "ok"


def test_failed_managed_migration_restores_verified_backup(monkeypatch, tmp_path):
    database = tmp_path / "workspace.db"
    _legacy_v1_database(database)

    def corrupt_then_fail(path):
        Path(path).write_bytes(b"partial migration output")
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(app_module, "GraphStore", corrupt_then_fail)
    with pytest.raises(DatabaseMigrationError) as raised:
        app_module._open_locked_store(database)
    assert raised.value.recovered is True
    assert raised.value.code == "databaseMigrationFailed"
    assert inspect_database_file(database)["graphVersion"] == 1
    manifests = list((tmp_path / "backups").glob("*.json"))
    assert len(manifests) == 1
    journal = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert journal["status"] == "restored"
    assert journal["restoredIntegrity"] == "ok"
    assert journal["failure"]["type"] == "RuntimeError"


def test_newer_schema_is_rejected_read_only_before_wal_or_migration(tmp_path):
    database = tmp_path / "future.db"
    conn = sqlite3.connect(database)
    try:
        conn.execute(
            "CREATE TABLE schema_migrations("
            "version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO schema_migrations VALUES(?, 'future')",
            [(version,) for version in range(1, 9)],
        )
        conn.commit()
    finally:
        conn.close()
    before = database.read_bytes()
    with pytest.raises(DatabaseSchemaError) as raised:
        GraphStore(database)
    assert raised.value.code == "databaseSchemaTooNew"
    assert database.read_bytes() == before
    assert not Path(str(database) + "-wal").exists()


def test_non_contiguous_migration_history_is_rejected(tmp_path):
    database = tmp_path / "damaged-history.db"
    conn = sqlite3.connect(database)
    try:
        conn.execute(
            "CREATE TABLE schema_migrations("
            "version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO schema_migrations VALUES(?, 'damaged')", [(1,), (3,)]
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(DatabaseSchemaError) as raised:
        GraphStore(database)
    assert raised.value.code == "databaseSchemaHistoryInvalid"


def test_current_database_does_not_create_redundant_startup_backup(monkeypatch, tmp_path):
    database = tmp_path / "workspace.db"
    current = GraphStore(database)
    current.close()
    monkeypatch.setenv("WEAVEPATH_DB", str(database))
    app = create_app()
    with TestClient(app) as client:
        status = client.get("/api/v1/system/database").json()
        assert status["status"] == "ready"
        assert status["backupPath"] is None
    assert not (tmp_path / "backups").exists()


def test_required_backup_failure_is_not_hidden_by_empty_temp_fallback(monkeypatch, tmp_path):
    database = tmp_path / "workspace.db"
    fallback = tmp_path / "fallback"
    _legacy_v1_database(database)
    monkeypatch.setenv("WEAVEPATH_DB", str(database))
    monkeypatch.setattr(app_module.tempfile, "gettempdir", lambda: str(fallback))

    def fail_backup(_path):
        raise DatabaseBackupError(database, OSError("backup volume unavailable"))

    monkeypatch.setattr(app_module, "prepare_database_upgrade", fail_backup)
    with pytest.raises(DatabaseBackupError) as raised:
        create_app()
    assert raised.value.code == "databaseBackupFailed"
    assert not fallback.exists()
