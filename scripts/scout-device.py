#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import Settings  # noqa: E402
from app.scout.auth import issue_device_token  # noqa: E402
from app.scout.store import ScoutStore  # noqa: E402


class MaintenanceLockBusy(RuntimeError):
    pass


def _store(settings: Settings) -> ScoutStore:
    settings.ensure_runtime_dirs()
    store = ScoutStore(settings.db_path)
    store.initialize()
    return store


@contextmanager
def _maintenance_lock(lock_path: Path | None = None):
    if lock_path is None:
        lock_path = PROJECT_ROOT / "runtime" / ".v2-maintenance.lock"
    lock_dir = lock_path.parent
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise MaintenanceLockBusy(
                "another V2 backup, restore, or device mutation is active"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _list_devices_readonly(db_path: Path) -> list[dict]:
    if not db_path.is_file() or db_path.is_symlink():
        raise FileNotFoundError(f"Scout database is unavailable: {db_path}")
    uri = db_path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            """
            SELECT id, name, enabled, capabilities_json, created_at, last_seen_at
            FROM scout_devices
            ORDER BY created_at
            """
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "enabled": bool(row["enabled"]),
            "capabilities": json.loads(row["capabilities_json"]),
            "created_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
        }
        for row in rows
    ]


def _reserve_private_output(path: Path) -> int:
    """Atomically reserve the credential path before changing device state."""

    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    return descriptor


def _write_reserved(descriptor: int, value: dict) -> None:
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _sync_parent_dir(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _enroll_to_file(
    store: ScoutStore, *, name: str, server_url: str, output: Path
) -> dict:
    descriptor = _reserve_private_output(output)
    record: dict | None = None
    try:
        # Keep the database record disabled until its one-time credential has
        # been durably delivered. A power loss can therefore leave only an
        # unusable orphan, never an active credential the operator did not see.
        record = store.enroll_device(name, enabled=False)
        provisioning = {
            "schema_version": "relicscope-scout-provisioning-v2",
            "server_url": server_url,
            **record,
        }
        _write_reserved(descriptor, provisioning)
        descriptor = -1
        _sync_parent_dir(output)
        store.set_device_enabled(record["device_id"], True)
        return record
    except Exception:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        output.unlink(missing_ok=True)
        if record is not None:
            # A credential that was not durably delivered must never remain enabled.
            store.set_device_enabled(record["device_id"], False)
        raise


def _rotate_to_file(
    store: ScoutStore, *, device_id: str, server_url: str, output: Path
) -> None:
    descriptor = _reserve_private_output(output)
    replacement_token = issue_device_token()
    provisioning = {
        "schema_version": "relicscope-scout-provisioning-v2",
        "server_url": server_url,
        "device_id": device_id,
        "device_token": replacement_token,
        "token_display_policy": "SHOW_ONCE",
    }
    try:
        _write_reserved(descriptor, provisioning)
        descriptor = -1
        _sync_parent_dir(output)
        store.rotate_device_token(device_id, replacement_token=replacement_token)
    except Exception:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        output.unlink(missing_ok=True)
        raise


def _secure_server_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(
            "Scout server URL must be an HTTPS origin without credentials or a path"
        )
    return value.rstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage paired RelicScope Scout devices")
    subcommands = parser.add_subparsers(dest="command", required=True)

    enroll = subcommands.add_parser("enroll", help="create a device and show its token once")
    enroll.add_argument("--name", required=True)
    enroll.add_argument("--server-url", required=True)
    enroll.add_argument("--output", type=Path, required=True)

    subcommands.add_parser("list", help="list devices without credentials")

    for action in ("enable", "disable", "rotate"):
        command = subcommands.add_parser(action)
        command.add_argument("device_id")
        if action == "rotate":
            command.add_argument("--server-url", required=True)
            command.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    settings = Settings.from_env()
    if args.command == "list":
        devices = _list_devices_readonly(settings.db_path)
        print(json.dumps({"devices": devices}, ensure_ascii=False, indent=2))
        return 0

    try:
        with _maintenance_lock():
            store = _store(settings)
            if args.command == "enroll":
                try:
                    server_url = _secure_server_url(args.server_url)
                except ValueError as exc:
                    parser.error(str(exc))
                record = _enroll_to_file(
                    store,
                    name=args.name,
                    server_url=server_url,
                    output=args.output,
                )
                print(
                    json.dumps(
                        {"written": str(args.output), "device_id": record["device_id"]}
                    )
                )
                return 0

            if args.command in {"enable", "disable"}:
                enabled = args.command == "enable"
                store.set_device_enabled(args.device_id, enabled)
                print(json.dumps({"device_id": args.device_id, "enabled": enabled}))
                return 0

            try:
                server_url = _secure_server_url(args.server_url)
            except ValueError as exc:
                parser.error(str(exc))
            _rotate_to_file(
                store,
                device_id=args.device_id,
                server_url=server_url,
                output=args.output,
            )
            print(
                json.dumps({"written": str(args.output), "device_id": args.device_id})
            )
            return 0
    except MaintenanceLockBusy as exc:
        parser.exit(3, f"FAIL: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
