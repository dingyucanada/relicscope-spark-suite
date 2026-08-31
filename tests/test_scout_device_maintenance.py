from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "relicscope_scout_device_maintenance",
    PROJECT_ROOT / "scripts" / "scout-device.py",
)
assert SPEC is not None and SPEC.loader is not None
SCOUT_DEVICE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCOUT_DEVICE)


def _device_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE scout_devices (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                token_salt TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                capabilities_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_seen_at TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO scout_devices VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "scout-test",
                "Gallery Scout",
                "salt",
                "hash",
                1,
                json.dumps({"capture": ["image/jpeg"]}),
                "2026-09-01T00:00:00Z",
                None,
            ),
        )


def test_list_devices_uses_read_only_database_without_maintenance_lock(
    tmp_path, monkeypatch, capsys
):
    database = tmp_path / "scout.sqlite3"
    _device_database(database)
    before = database.stat().st_mtime_ns

    monkeypatch.setattr(
        SCOUT_DEVICE,
        "Settings",
        SimpleNamespace(from_env=lambda: SimpleNamespace(db_path=database)),
    )
    monkeypatch.setattr(
        SCOUT_DEVICE,
        "_maintenance_lock",
        lambda: pytest.fail("list must not acquire the maintenance lock"),
    )
    monkeypatch.setattr(
        SCOUT_DEVICE,
        "_store",
        lambda _settings: pytest.fail("list must not initialize or mutate the store"),
    )
    monkeypatch.setattr(sys, "argv", ["scout-device.py", "list"])

    assert SCOUT_DEVICE.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["devices"] == [
        {
            "id": "scout-test",
            "name": "Gallery Scout",
            "enabled": True,
            "capabilities": {"capture": ["image/jpeg"]},
            "created_at": "2026-09-01T00:00:00Z",
            "last_seen_at": None,
        }
    ]
    assert database.stat().st_mtime_ns == before
    assert not database.with_name(f"{database.name}-wal").exists()
    assert not database.with_name(f"{database.name}-shm").exists()


def test_mutation_holds_maintenance_lock_around_store_write(
    tmp_path, monkeypatch, capsys
):
    events: list[str] = []

    @contextmanager
    def fake_lock():
        events.append("lock-enter")
        yield
        events.append("lock-exit")

    class FakeStore:
        def set_device_enabled(self, device_id: str, enabled: bool) -> None:
            events.append(f"disable:{device_id}:{enabled}")

    monkeypatch.setattr(
        SCOUT_DEVICE,
        "Settings",
        SimpleNamespace(from_env=lambda: SimpleNamespace(db_path=tmp_path / "db")),
    )
    monkeypatch.setattr(SCOUT_DEVICE, "_maintenance_lock", fake_lock)
    monkeypatch.setattr(SCOUT_DEVICE, "_store", lambda _settings: FakeStore())
    monkeypatch.setattr(
        sys, "argv", ["scout-device.py", "disable", "scout-test"]
    )

    assert SCOUT_DEVICE.main() == 0
    assert events == ["lock-enter", "disable:scout-test:False", "lock-exit"]
    assert json.loads(capsys.readouterr().out) == {
        "device_id": "scout-test",
        "enabled": False,
    }


def test_python_lock_conflicts_with_backup_restore_flock(tmp_path):
    lock_path = tmp_path / ".v2-maintenance.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SCOUT_DEVICE.MaintenanceLockBusy):
            with SCOUT_DEVICE._maintenance_lock(lock_path):
                pass
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    with SCOUT_DEVICE._maintenance_lock(lock_path):
        assert lock_path.stat().st_mode & 0o777 == 0o600
