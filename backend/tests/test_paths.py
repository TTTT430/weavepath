from pathlib import Path

from api.app import default_database_path


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
