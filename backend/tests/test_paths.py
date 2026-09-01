import errno
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import api.app as app_module
import pytest
from fastapi.testclient import TestClient

from api.app import (DatabaseInstanceLockError, create_app, default_database_path,
                     open_default_store)
from graph_core import GraphStore


def test_importing_app_module_does_not_create_default_database(tmp_path):
    database = tmp_path / "import-only" / "workspace.db"
    environment = os.environ.copy()
    environment["WEAVEPATH_DB"] = str(database)

    result = subprocess.run(
        [sys.executable, "-c", "import api.app"],
        check=False,
        capture_output=True,
        cwd=Path(__file__).parents[1],
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not database.exists()
    assert not database.parent.exists()


def test_unwritable_default_database_uses_the_narrow_temp_fallback(monkeypatch, tmp_path):
    calls = []
    sentinel = object()

    def fake_store(path):
        calls.append(Path(path))
        if len(calls) == 1:
            raise sqlite3.OperationalError("unable to open database file")
        return sentinel

    monkeypatch.setattr(app_module, "GraphStore", fake_store)
    monkeypatch.setattr(app_module.tempfile, "gettempdir", lambda: str(tmp_path))
    assert open_default_store() is sentinel
    assert calls[1] == tmp_path / "WeavePath" / "data" / "workspace.db"


def test_default_database_does_not_hide_other_sqlite_failures(monkeypatch):
    def fake_store(path):
        del path
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(app_module, "GraphStore", fake_store)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        open_default_store()


def test_data_dir_and_file_override(monkeypatch, tmp_path):
    direct = tmp_path / "weavepath.db"
    monkeypatch.setenv("WEAVEPATH_DB", str(direct))
    monkeypatch.setenv("WEAVEPATH_DATA_DIR", str(tmp_path / "ignored-new"))
    monkeypatch.setenv("COTHINKER_WORKFLOW_DB", str(tmp_path / "ignored-old.db"))
    monkeypatch.setenv("COTHINKER_DATA_DIR", str(tmp_path / "ignored-old"))
    assert default_database_path() == str(direct)

    monkeypatch.delenv("WEAVEPATH_DB")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("WEAVEPATH_DATA_DIR", str(data_dir))
    assert default_database_path() == str(data_dir / "workspace.db")


def test_legacy_environment_overrides_remain_supported(monkeypatch, tmp_path):
    monkeypatch.delenv("WEAVEPATH_DB", raising=False)
    monkeypatch.delenv("WEAVEPATH_DATA_DIR", raising=False)
    direct = tmp_path / "legacy.db"
    monkeypatch.setenv("COTHINKER_WORKFLOW_DB", str(direct))
    monkeypatch.setenv("COTHINKER_DATA_DIR", str(tmp_path / "ignored"))
    assert default_database_path() == str(direct)
    monkeypatch.delenv("COTHINKER_WORKFLOW_DB")
    legacy_dir = tmp_path / "legacy-data"
    monkeypatch.setenv("COTHINKER_DATA_DIR", str(legacy_dir))
    assert default_database_path() == str(legacy_dir / "workspace.db")


def test_windows_default_shape(monkeypatch, tmp_path):
    monkeypatch.delenv("WEAVEPATH_DB", raising=False)
    monkeypatch.delenv("WEAVEPATH_DATA_DIR", raising=False)
    monkeypatch.delenv("COTHINKER_WORKFLOW_DB", raising=False)
    monkeypatch.delenv("COTHINKER_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert Path(default_database_path()) == tmp_path / "WeavePath" / "data" / "workspace.db"


def test_existing_legacy_database_is_used_until_new_database_exists(monkeypatch, tmp_path):
    for name in ["WEAVEPATH_DB", "WEAVEPATH_DATA_DIR", "COTHINKER_WORKFLOW_DB", "COTHINKER_DATA_DIR"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    legacy = tmp_path / "CoThinker Workspace" / "data" / "workspace.db"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy")
    assert Path(default_database_path()) == legacy

    current = tmp_path / "WeavePath" / "data" / "workspace.db"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"current")
    assert Path(default_database_path()) == current


def test_posix_legacy_slug_is_discovered(monkeypatch, tmp_path):
    for name in ["WEAVEPATH_DB", "WEAVEPATH_DATA_DIR", "COTHINKER_WORKFLOW_DB", "COTHINKER_DATA_DIR", "LOCALAPPDATA"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    legacy = tmp_path / "co-thinker-workspace" / "data" / "workspace.db"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy")
    assert Path(default_database_path()) == legacy


def test_injected_store_and_default_memory_store_do_not_take_process_lock(monkeypatch):
    def unexpected_lock(_):
        raise AssertionError("process lock must not be acquired")

    monkeypatch.setattr(app_module._ProcessFileLock, "acquire", unexpected_lock)

    injected = GraphStore(":memory:")
    with TestClient(create_app(injected)) as client:
        assert client.get("/api/v1/health").status_code == 200
    injected.close()

    monkeypatch.setenv("WEAVEPATH_DB", ":memory:")
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/health").status_code == 200


def test_unwritable_instance_lock_uses_the_same_narrow_temp_fallback(monkeypatch, tmp_path):
    primary = tmp_path / "denied" / "workspace.db"
    fallback_root = tmp_path / "fallback"
    monkeypatch.setenv("WEAVEPATH_DB", str(primary))
    monkeypatch.setattr(app_module.tempfile, "gettempdir", lambda: str(fallback_root))
    real_acquire = app_module._ProcessFileLock.acquire

    def deny_primary(lock):
        if lock.database_path == str(primary.resolve()):
            raise PermissionError(errno.EACCES, "permission denied", str(lock.path))
        return real_acquire(lock)

    monkeypatch.setattr(app_module._ProcessFileLock, "acquire", deny_primary)
    app = create_app()
    assert Path(app.state.store.db_path) == fallback_root / "WeavePath" / "data" / "workspace.db"
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200


def test_non_access_lock_failure_is_not_hidden_by_temp_fallback(monkeypatch, tmp_path):
    primary = tmp_path / "workspace.db"
    fallback_root = tmp_path / "fallback"
    monkeypatch.setenv("WEAVEPATH_DB", str(primary))
    monkeypatch.setattr(app_module.tempfile, "gettempdir", lambda: str(fallback_root))

    def fail_lock(_):
        raise OSError(errno.EIO, "simulated lock I/O failure")

    monkeypatch.setattr(app_module._ProcessFileLock, "acquire", fail_lock)
    with pytest.raises(OSError, match="simulated lock I/O failure"):
        create_app()
    assert not fallback_root.exists()


def test_second_process_cannot_recover_runs_and_lock_releases_for_restart(monkeypatch, tmp_path):
    database = tmp_path / "workspace.db"
    monkeypatch.setenv("WEAVEPATH_DB", str(database))

    first = create_app()
    with TestClient(first):
        graph = first.state.store.create_workflow(
            name="single instance", root_title="A", root_instance_id="A"
        )
        workflow_id = graph["workflowId"]
        run, created = first.state.agent_runs.create(
            workflow_id=workflow_id,
            instance_id="A",
            request={
                "objective": "remain owned by the first process",
                "constraints": [],
                "deliverables": [],
                "acceptanceChecks": [],
                "expectedContentRevision": 0,
                "idempotencyKey": "single-instance-regression",
            },
            context={"memoryRoute": [], "availableTools": [], "messages": []},
            model_snapshot={"adapter": "test"},
        )
        assert created
        first.state.agent_runs.start(run["runId"])

        result = subprocess.run(
            [sys.executable, "-c", "from api.app import create_app; create_app()"],
            check=False,
            capture_output=True,
            cwd=Path(__file__).parents[1],
            env=os.environ.copy(),
            text=True,
            timeout=10,
        )

        assert result.returncode != 0
        assert DatabaseInstanceLockError.code in result.stderr
        assert "Another WeavePath backend is already using database" in result.stderr
        assert first.state.agent_runs.get(run["runId"])["status"] == "running"
        event_types = {
            item["type"] for item in first.state.agent_runs.events(run["runId"], 0, 100)["events"]
        }
        assert "run.interrupted" not in event_types

    # The lock file stays in place, while the OS ownership is released by the
    # completed lifespan. A new process/app can then acquire it and perform the
    # legitimate startup recovery exactly once.
    lock_path = Path(str(database.resolve()) + ".weavepath.lock")
    assert lock_path.exists()
    restarted = create_app()
    with TestClient(restarted) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert restarted.state.agent_runs.get(run["runId"])["status"] == "interrupted"
        events = restarted.state.agent_runs.events(run["runId"], 0, 100)["events"]
        assert [item["type"] for item in events].count("run.interrupted") == 1
    assert lock_path.exists()


def test_startup_failure_releases_owned_store_lock(monkeypatch, tmp_path):
    database = tmp_path / "workspace.db"
    monkeypatch.setenv("WEAVEPATH_DB", str(database))
    failing = create_app()

    def fail_recovery():
        raise RuntimeError("simulated startup recovery failure")

    monkeypatch.setattr(failing.state.agent_runs, "recover_interrupted", fail_recovery)
    with pytest.raises(RuntimeError, match="simulated startup recovery failure"):
        with TestClient(failing):
            pass

    # The lifespan finally block closes SQLite and releases the process lock
    # even when startup never reaches its yield point.
    restarted = create_app()
    with TestClient(restarted) as client:
        assert client.get("/api/v1/health").status_code == 200


def test_factory_setup_failure_releases_owned_store_lock(monkeypatch, tmp_path):
    database = tmp_path / "workspace.db"
    monkeypatch.setenv("WEAVEPATH_DB", str(database))
    real_repository = app_module.AgentRunRepository

    def fail_repository(*_):
        raise RuntimeError("simulated factory setup failure")

    monkeypatch.setattr(app_module, "AgentRunRepository", fail_repository)
    with pytest.raises(RuntimeError, match="simulated factory setup failure"):
        create_app()

    monkeypatch.setattr(app_module, "AgentRunRepository", real_repository)
    restarted = create_app()
    with TestClient(restarted) as client:
        assert client.get("/api/v1/health").status_code == 200
