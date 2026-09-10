"""Release-safe lifecycle helpers for the local SQLite workspace.

The live database is never used as its own rollback artifact.  A versioned,
integrity-checked SQLite snapshot is created before a managed startup applies
schema migrations.  If opening or verification fails, the exact database file
is restored while the process-scoped API lock is still held.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from graph_core.migrations import (
    LATEST_GRAPH_SCHEMA_VERSION,
    LATEST_RUNTIME_SCHEMA_VERSION,
    assert_schema_compatible,
    inspect_schema_versions,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    value = str(row[0]) if row else "missing-result"
    if value.lower() != "ok":
        raise sqlite3.DatabaseError(f"database integrity check failed: {value}")
    return "ok"


def _read_only_connection(path: Path) -> sqlite3.Connection:
    # pathlib.as_uri handles Windows drive letters and escaping correctly.
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)


def inspect_database_file(database_path: str | Path) -> dict[str, Any]:
    path = Path(database_path).resolve(strict=False)
    if not path.exists() or path.stat().st_size == 0:
        return {
            "databasePath": str(path),
            "exists": path.exists(),
            "graphVersion": 0,
            "runtimeVersion": 0,
            "graphVersions": [],
            "runtimeVersions": [],
            "integrity": "new",
        }
    conn = _read_only_connection(path)
    try:
        versions = assert_schema_compatible(conn)
        integrity = _integrity(conn)
    finally:
        conn.close()
    return {
        "databasePath": str(path),
        "exists": True,
        "graphVersion": versions["graphVersion"],
        "runtimeVersion": versions["runtimeVersion"],
        "graphVersions": list(versions["graphVersions"]),
        "runtimeVersions": list(versions["runtimeVersions"]),
        "integrity": integrity,
    }


def assert_database_file_compatible(database_path: str | Path) -> None:
    """Read-only compatibility guard used before SQLite journal changes."""
    path = Path(database_path).resolve(strict=False)
    if not path.exists() or path.stat().st_size == 0:
        return
    conn = _read_only_connection(path)
    try:
        assert_schema_compatible(conn)
    finally:
        conn.close()


@dataclass(frozen=True)
class DatabaseUpgrade:
    database_path: Path
    backup_path: Path
    manifest_path: Path
    source: dict[str, Any]


class DatabaseMigrationError(RuntimeError):
    """Stable startup error raised after a failed migration was handled."""

    code = "databaseMigrationFailed"

    def __init__(self, database_path: Path, backup_path: Path | None,
                 *, recovered: bool, cause: BaseException) -> None:
        self.database_path = str(database_path)
        self.backup_path = str(backup_path) if backup_path else None
        self.recovered = recovered
        recovery = "The pre-migration database was restored." if recovered else (
            "No migration backup was available; the database was not restored."
        )
        super().__init__(f"{self.code}: Database startup migration failed. {recovery} Cause: {cause}")


class DatabaseBackupError(RuntimeError):
    """Raised when a required pre-migration backup cannot be completed."""

    code = "databaseBackupFailed"

    def __init__(self, database_path: Path, cause: BaseException) -> None:
        self.database_path = str(database_path)
        super().__init__(
            f"{self.code}: Refusing to migrate '{database_path}' because its "
            f"verified backup could not be created. Cause: {cause}"
        )


class DatabaseRestoreError(RuntimeError):
    code = "databaseRestoreFailed"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _backup_directory(database_path: str | Path) -> Path:
    return Path(database_path).resolve(strict=False).parent / "backups"


def list_database_backups(database_path: str | Path) -> list[dict[str, Any]]:
    """List only manifests bound to this exact database, newest first."""
    database = Path(database_path).resolve(strict=False)
    directory = _backup_directory(database)
    if not directory.exists():
        return []
    items: list[dict[str, Any]] = []
    for manifest_path in directory.glob("*.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            backup = Path(str(manifest["backupPath"])).resolve(strict=True)
            if Path(str(manifest["databasePath"])).resolve(strict=False) != database:
                continue
            if backup.parent != directory.resolve(strict=False):
                continue
            expected_sha = manifest.get("backupSha256")
            actual_sha = _sha256(backup)
            valid = isinstance(expected_sha, str) and expected_sha == actual_sha
            integrity = inspect_database_file(backup)["integrity"] if valid else "checksumMismatch"
            items.append({
                "manifestPath": str(manifest_path.resolve()),
                "backupPath": str(backup),
                "status": manifest.get("status", "unknown"),
                "source": manifest.get("source", {}),
                "target": manifest.get("target", {}),
                "preparedAt": manifest.get("preparedAt"),
                "completedAt": manifest.get("completedAt"),
                "restoredAt": manifest.get("restoredAt"),
                "sizeBytes": backup.stat().st_size,
                "sha256": actual_sha,
                "integrity": integrity,
                "restorable": valid and integrity == "ok" and manifest.get("status") in {"completed", "restored"},
            })
        except (OSError, ValueError, KeyError, sqlite3.Error):
            items.append({
                "manifestPath": str(manifest_path.resolve(strict=False)),
                "backupPath": None,
                "status": "invalid",
                "integrity": "invalidManifest",
                "restorable": False,
            })
    return sorted(items, key=lambda item: str(item.get("preparedAt") or item["manifestPath"]), reverse=True)


def database_backup_retention_plan(database_path: str | Path, keep_last: int) -> dict[str, Any]:
    if keep_last < 1 or keep_last > 100:
        raise ValueError("keepLast must be between 1 and 100")
    backups = list_database_backups(database_path)
    valid = [item for item in backups if item.get("restorable")]
    removable = valid[keep_last:]
    return {
        "keepLast": keep_last,
        "backupCount": len(backups),
        "restorableCount": len(valid),
        "remove": removable,
        "removeCount": len(removable),
    }


def commit_database_backup_retention(database_path: str | Path, keep_last: int,
                                     *, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("backup cleanup requires explicit confirmation")
    plan = database_backup_retention_plan(database_path, keep_last)
    removed: list[str] = []
    for item in plan["remove"]:
        # The listing step proved both targets are exact children of this
        # database's backup directory. Never expand globs during deletion.
        backup = Path(item["backupPath"])
        manifest = Path(item["manifestPath"])
        backup.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        removed.extend([str(backup), str(manifest)])
    return {**plan, "removedPaths": removed}


@contextmanager
def _offline_database_lock(database_path: Path):
    lock_path = Path(str(database_path) + ".weavepath.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    locked = False
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise DatabaseRestoreError("stop the WeavePath API before restoring a backup") from exc
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def restore_database_from_manifest(database_path: str | Path, manifest_path: str | Path,
                                   *, confirmation: str) -> dict[str, Any]:
    """Restore a verified migration snapshot while the API is stopped."""
    database = Path(database_path).resolve(strict=False)
    manifest_file = Path(manifest_path).resolve(strict=True)
    expected_confirmation = f"RESTORE {database.name}"
    if confirmation != expected_confirmation:
        raise DatabaseRestoreError(f"confirmation must equal '{expected_confirmation}'")
    matches = {
        item["manifestPath"]: item for item in list_database_backups(database)
        if item.get("restorable")
    }
    selected = matches.get(str(manifest_file))
    if selected is None:
        raise DatabaseRestoreError("manifest is not a verified restorable backup for this database")
    backup = Path(selected["backupPath"])
    with _offline_database_lock(database):
        staged = database.with_name(f".{database.name}.manual-restore-{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(backup, staged)
            if _sha256(staged) != selected["sha256"]:
                raise DatabaseRestoreError("staged restore checksum mismatch")
            if inspect_database_file(staged)["integrity"] != "ok":
                raise DatabaseRestoreError("staged restore integrity check failed")
            for suffix in ("-wal", "-shm"):
                Path(str(database) + suffix).unlink(missing_ok=True)
            os.replace(staged, database)
        finally:
            staged.unlink(missing_ok=True)
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
        receipt = {
            "restoredAt": _now(),
            "databaseSha256": _sha256(database),
            "databaseIntegrity": inspect_database_file(database)["integrity"],
            "mode": "explicitOfflineRestore",
        }
        _write_manifest(manifest_file, {**payload, "status": "restored", **receipt})
        return {
            "databasePath": str(database), "manifestPath": str(manifest_file),
            "backupPath": str(backup), **receipt,
        }


def _manifest(upgrade: DatabaseUpgrade, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "formatVersion": 1,
        "status": status,
        "databasePath": str(upgrade.database_path),
        "backupPath": str(upgrade.backup_path),
        "source": upgrade.source,
        "target": {
            "graphVersion": LATEST_GRAPH_SCHEMA_VERSION,
            "runtimeVersion": LATEST_RUNTIME_SCHEMA_VERSION,
        },
        **extra,
    }


def prepare_database_upgrade(database_path: str | Path) -> DatabaseUpgrade | None:
    """Create a verified backup iff an existing database needs migration."""
    path = Path(database_path).resolve(strict=False)
    source = inspect_database_file(path)
    if not source["exists"] or (
        source["graphVersion"] == LATEST_GRAPH_SCHEMA_VERSION
        and source["runtimeVersion"] == LATEST_RUNTIME_SCHEMA_VERSION
    ):
        return None

    try:
        backup_dir = path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        base = (
            f"{path.stem}.pre-g{source['graphVersion']}-r{source['runtimeVersion']}"
            f"-to-g{LATEST_GRAPH_SCHEMA_VERSION}-r{LATEST_RUNTIME_SCHEMA_VERSION}"
            f"-{_stamp()}-{uuid.uuid4().hex[:8]}"
        )
        backup_path = backup_dir / f"{base}.db"
        manifest_path = backup_dir / f"{base}.json"
        temporary = backup_dir / f".{base}.db.tmp"
        try:
            source_conn: sqlite3.Connection | None = None
            backup_conn: sqlite3.Connection | None = None
            try:
                source_conn = _read_only_connection(path)
                backup_conn = sqlite3.connect(temporary)
                source_conn.backup(backup_conn)
                _integrity(backup_conn)
                backup_conn.commit()
            finally:
                if backup_conn is not None:
                    backup_conn.close()
                if source_conn is not None:
                    source_conn.close()
            os.replace(temporary, backup_path)
        finally:
            if temporary.exists():
                temporary.unlink()

        upgrade = DatabaseUpgrade(path, backup_path, manifest_path, source)
        _write_manifest(manifest_path, _manifest(
            upgrade,
            "prepared",
            preparedAt=_now(),
            backupSha256=_sha256(backup_path),
            backupIntegrity="ok",
        ))
        return upgrade
    except (OSError, sqlite3.Error) as exc:
        raise DatabaseBackupError(path, exc) from exc


def complete_database_upgrade(upgrade: DatabaseUpgrade | None,
                              conn: sqlite3.Connection, database_path: str | Path) -> dict[str, Any]:
    """Verify the opened store and close the pre-migration journal."""
    versions = assert_schema_compatible(conn)
    integrity = _integrity(conn)
    if (versions["graphVersion"] != LATEST_GRAPH_SCHEMA_VERSION
            or versions["runtimeVersion"] != LATEST_RUNTIME_SCHEMA_VERSION):
        raise sqlite3.DatabaseError(
            "database migration did not reach the supported schema versions"
        )
    display_path = (":memory:" if str(database_path) == ":memory:"
                    else str(Path(database_path).resolve(strict=False)))
    result = {
        "databasePath": display_path,
        "status": "migrated" if upgrade else "ready",
        "graphVersion": versions["graphVersion"],
        "runtimeVersion": versions["runtimeVersion"],
        "targetGraphVersion": LATEST_GRAPH_SCHEMA_VERSION,
        "targetRuntimeVersion": LATEST_RUNTIME_SCHEMA_VERSION,
        "integrity": integrity,
        "backupPath": str(upgrade.backup_path) if upgrade else None,
        "backupManifestPath": str(upgrade.manifest_path) if upgrade else None,
    }
    if upgrade:
        prepared = json.loads(upgrade.manifest_path.read_text(encoding="utf-8"))
        _write_manifest(upgrade.manifest_path, {
            **prepared,
            **_manifest(upgrade, "completed"),
            "completedAt": _now(),
            "result": {
                "graphVersion": versions["graphVersion"],
                "runtimeVersion": versions["runtimeVersion"],
                "integrity": integrity,
            },
        })
    return result


def restore_database_upgrade(upgrade: DatabaseUpgrade, cause: BaseException) -> None:
    """Atomically restore a verified snapshot after a failed managed startup."""
    expected_sha = _sha256(upgrade.backup_path)
    backup_conn = _read_only_connection(upgrade.backup_path)
    try:
        _integrity(backup_conn)
    finally:
        backup_conn.close()

    temporary = upgrade.database_path.with_name(
        f".{upgrade.database_path.name}.restore-{uuid.uuid4().hex}.tmp"
    )
    try:
        shutil.copy2(upgrade.backup_path, temporary)
        if _sha256(temporary) != expected_sha:
            raise OSError("restored database staging copy failed checksum verification")
        # The managed caller still owns the exact database lock and has closed
        # SQLite before this function runs. Removing these exact sidecars keeps
        # a failed migration WAL from being replayed over the restored image.
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(upgrade.database_path) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        os.replace(temporary, upgrade.database_path)
    finally:
        if temporary.exists():
            temporary.unlink()

    manifest = json.loads(upgrade.manifest_path.read_text(encoding="utf-8"))
    _write_manifest(upgrade.manifest_path, {
        **manifest,
        **_manifest(upgrade, "restored"),
        "restoredAt": _now(),
        "failure": {"type": type(cause).__name__, "message": str(cause)},
        "restoredSha256": _sha256(upgrade.database_path),
        "restoredIntegrity": inspect_database_file(upgrade.database_path)["integrity"],
    })


def connection_database_status(conn: sqlite3.Connection, database_path: str | Path,
                               lifecycle: dict[str, Any] | None = None) -> dict[str, Any]:
    versions = inspect_schema_versions(conn)
    return {
        "databasePath": str(database_path),
        "status": (lifecycle or {}).get("status", "ready"),
        "graphVersion": versions["graphVersion"],
        "runtimeVersion": versions["runtimeVersion"],
        "targetGraphVersion": LATEST_GRAPH_SCHEMA_VERSION,
        "targetRuntimeVersion": LATEST_RUNTIME_SCHEMA_VERSION,
        "integrity": (lifecycle or {}).get("integrity", "notChecked"),
        "backupPath": (lifecycle or {}).get("backupPath"),
        "backupManifestPath": (lifecycle or {}).get("backupManifestPath"),
        "downgradeSupported": False,
        "rollbackStrategy": "restoreVerifiedPreMigrationBackup",
    }


__all__ = [
    "DatabaseMigrationError",
    "DatabaseBackupError",
    "DatabaseRestoreError",
    "DatabaseUpgrade",
    "assert_database_file_compatible",
    "complete_database_upgrade",
    "commit_database_backup_retention",
    "connection_database_status",
    "database_backup_retention_plan",
    "inspect_database_file",
    "list_database_backups",
    "prepare_database_upgrade",
    "restore_database_upgrade",
    "restore_database_from_manifest",
]
