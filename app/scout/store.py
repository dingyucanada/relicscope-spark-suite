from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.store import canonical_json, utc_now

from .auth import hash_device_token, issue_device_token, verify_device_token


TERMINAL_STATUSES = {
    "SUCCEEDED",
    "PARTIAL",
    "NEEDS_RECAPTURE",
    "MODEL_UNAVAILABLE",
    "FAILED",
    "CANCELLED",
}


class ScoutAuthenticationError(PermissionError):
    pass


class ScoutConflict(ValueError):
    pass


class ScoutCapacityError(RuntimeError):
    pass


class ScoutJobNotFound(KeyError):
    pass


class ScoutStore:
    """SQLite-backed device registry and durable Scout job queue.

    The primary Spark owns this state. GPU workers receive immutable job inputs and never
    become the source of truth for devices, media, or results.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scout_devices (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    token_salt TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    capabilities_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT
                );

                CREATE TABLE IF NOT EXISTS scout_jobs (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    client_job_id TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    attempt_base INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    error_code TEXT,
                    error_detail TEXT,
                    next_attempt_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    FOREIGN KEY(device_id) REFERENCES scout_devices(id),
                    UNIQUE(device_id, client_job_id)
                );
                CREATE INDEX IF NOT EXISTS idx_scout_jobs_queue
                ON scout_jobs(status, created_at);

                CREATE TABLE IF NOT EXISTS scout_captures (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    client_capture_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL DEFAULT 0,
                    filename TEXT NOT NULL,
                    view_code TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    byte_count INTEGER NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    device_quality_json TEXT,
                    server_quality_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES scout_jobs(id) ON DELETE CASCADE,
                    UNIQUE(job_id, client_capture_id),
                    UNIQUE(job_id, sha256)
                );
                CREATE INDEX IF NOT EXISTS idx_scout_captures_job
                ON scout_captures(job_id, created_at);

                CREATE TABLE IF NOT EXISTS scout_job_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES scout_jobs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_scout_events_job
                ON scout_job_events(job_id, seq);
                """
            )
            job_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(scout_jobs)").fetchall()
            }
            if "next_attempt_at" not in job_columns:
                connection.execute(
                    "ALTER TABLE scout_jobs ADD COLUMN next_attempt_at TEXT"
                )
            if "attempt_base" not in job_columns:
                connection.execute(
                    "ALTER TABLE scout_jobs "
                    "ADD COLUMN attempt_base INTEGER NOT NULL DEFAULT 0"
                )
            capture_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(scout_captures)"
                ).fetchall()
            }
            if "ordinal" not in capture_columns:
                connection.execute(
                    "ALTER TABLE scout_captures ADD COLUMN ordinal INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_scout_jobs_retry
                ON scout_jobs(status, next_attempt_at, created_at)
                """
            )

    @staticmethod
    def _device_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "enabled": bool(row["enabled"]),
            "capabilities": json.loads(row["capabilities_json"]),
            "created_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
        }

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> dict[str, Any]:
        keys = set(row.keys())
        capture_count = int(row["capture_count"]) if "capture_count" in keys else 0
        return {
            "id": row["id"],
            "device_id": row["device_id"],
            "client_job_id": row["client_job_id"],
            "payload_sha256": row["payload_sha256"],
            "request": json.loads(row["request_json"]),
            "status": row["status"],
            "stage": row["stage"],
            "attempt": int(row["attempt"]),
            "attempt_base": int(row["attempt_base"]),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error_code": row["error_code"],
            "error_detail": row["error_detail"],
            "next_attempt_at": row["next_attempt_at"],
            "capture_count": capture_count,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "result_available": bool(row["result_json"]),
        }

    def enroll_device(
        self,
        name: str,
        capabilities: dict[str, Any] | None = None,
        *,
        enabled: bool = True,
    ) -> dict[str, Any]:
        normalized = " ".join(name.split())
        if not normalized or len(normalized) > 120:
            raise ValueError("device name is invalid")
        device_id = f"scout-{uuid4().hex[:16]}"
        token = issue_device_token()
        salt, token_hash = hash_device_token(token)
        created_at = utc_now()
        capability_record = capabilities or {
            "capture": ["image/jpeg", "image/png", "image/webp"],
            "transport": ["wifi", "usb-network"],
            "local_quality": "optional",
        }
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO scout_devices
                    (id, name, token_salt, token_hash, enabled, capabilities_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    device_id,
                    normalized,
                    salt,
                    token_hash,
                    int(enabled),
                    canonical_json(capability_record),
                    created_at,
                ),
            )
        return {
            "device_id": device_id,
            "device_token": token,
            "name": normalized,
            "created_at": created_at,
            "token_display_policy": "SHOW_ONCE",
        }

    def rotate_device_token(
        self, device_id: str, *, replacement_token: str | None = None
    ) -> dict[str, str]:
        token = replacement_token or issue_device_token()
        salt, token_hash = hash_device_token(token)
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE scout_devices SET token_salt = ?, token_hash = ? WHERE id = ?",
                (salt, token_hash, device_id),
            )
            if cursor.rowcount != 1:
                raise ScoutAuthenticationError("unknown Scout device")
        return {
            "device_id": device_id,
            "device_token": token,
            "token_display_policy": "SHOW_ONCE",
        }

    def set_device_enabled(self, device_id: str, enabled: bool) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE scout_devices SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, device_id),
            )
            if cursor.rowcount != 1:
                raise ScoutAuthenticationError("unknown Scout device")

    def authenticate_device(self, device_id: str, token: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM scout_devices WHERE id = ?", (device_id,)
            ).fetchone()
            if (
                row is None
                or not bool(row["enabled"])
                or not verify_device_token(token, row["token_salt"], row["token_hash"])
            ):
                raise ScoutAuthenticationError("invalid or disabled Scout credentials")
            now = utc_now()
            connection.execute(
                "UPDATE scout_devices SET last_seen_at = ? WHERE id = ?",
                (now, device_id),
            )
            mutable = dict(row)
            mutable["last_seen_at"] = now
            return {
                "id": mutable["id"],
                "name": mutable["name"],
                "enabled": bool(mutable["enabled"]),
                "capabilities": json.loads(mutable["capabilities_json"]),
                "created_at": mutable["created_at"],
                "last_seen_at": now,
            }

    def get_device(self, device_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM scout_devices WHERE id = ?", (device_id,)
            ).fetchone()
        if row is None or not bool(row["enabled"]):
            raise ScoutAuthenticationError("unknown or disabled Scout device")
        return self._device_from_row(row)

    def list_devices(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM scout_devices ORDER BY created_at"
            ).fetchall()
        return [self._device_from_row(row) for row in rows]

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        job_id: str,
        event_type: str,
        stage: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO scout_job_events
                (job_id, event_type, stage, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, event_type, stage, canonical_json(payload or {}), utc_now()),
        )

    def find_idempotent_job(
        self, device_id: str, client_job_id: str
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT j.*, (SELECT COUNT(*) FROM scout_captures c WHERE c.job_id = j.id)
                    AS capture_count
                FROM scout_jobs j
                WHERE j.device_id = ? AND j.client_job_id = ?
                """,
                (device_id, client_job_id),
            ).fetchone()
        return self._job_from_row(row) if row else None

    def create_job(
        self,
        *,
        device_id: str,
        client_job_id: str,
        payload_sha256: str,
        request: dict[str, Any],
        captures: list[dict[str, Any]],
        max_outstanding_jobs: int,
    ) -> tuple[dict[str, Any], bool]:
        if max_outstanding_jobs < 1:
            raise ValueError("max_outstanding_jobs must be positive")
        job_id = f"job-{uuid4().hex}"
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM scout_jobs WHERE device_id = ? AND client_job_id = ?",
                    (device_id, client_job_id),
                ).fetchone()
                if existing is not None:
                    if existing["payload_sha256"] != payload_sha256:
                        raise ScoutConflict(
                            "client_job_id already exists with different immutable input"
                        )
                    connection.execute("COMMIT")
                    return self.get_job(existing["id"], device_id=device_id), False
                outstanding = connection.execute(
                    """
                    SELECT COUNT(*) AS value FROM scout_jobs
                    WHERE device_id = ?
                      AND status IN ('QUEUED', 'RUNNING', 'RETRY_WAIT')
                    """,
                    (device_id,),
                ).fetchone()
                if int(outstanding["value"]) >= max_outstanding_jobs:
                    raise ScoutCapacityError(
                        "Scout device has too many outstanding jobs"
                    )
                connection.execute(
                    """
                    INSERT INTO scout_jobs
                        (id, device_id, client_job_id, payload_sha256, request_json,
                         status, stage, attempt, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 'QUEUED', 'INGEST_VALIDATION', 0, ?, ?)
                    """,
                    (
                        job_id,
                        device_id,
                        client_job_id,
                        payload_sha256,
                        canonical_json(request),
                        now,
                        now,
                    ),
                )
                for capture in captures:
                    connection.execute(
                        """
                        INSERT INTO scout_captures
                            (id, job_id, client_capture_id, ordinal, filename, view_code,
                             mime_type, sha256, byte_count, width, height, path,
                             captured_at, device_quality_json, server_quality_json,
                             created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            capture["id"],
                            job_id,
                            capture["client_capture_id"],
                            capture["ordinal"],
                            capture["filename"],
                            capture["view_code"],
                            capture["mime_type"],
                            capture["sha256"],
                            capture["byte_count"],
                            capture["width"],
                            capture["height"],
                            capture["path"],
                            capture["captured_at"],
                            canonical_json(capture.get("device_quality"))
                            if capture.get("device_quality") is not None
                            else None,
                            canonical_json(capture["server_quality"]),
                            now,
                        ),
                    )
                self._append_event(
                    connection,
                    job_id,
                    "JOB_ACCEPTED",
                    "INGEST_VALIDATION",
                    {
                        "payload_sha256": payload_sha256,
                        "capture_count": len(captures),
                    },
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get_job(job_id, device_id=device_id), True

    def get_job(self, job_id: str, *, device_id: str | None = None) -> dict[str, Any]:
        where = "j.id = ?"
        parameters: tuple[Any, ...] = (job_id,)
        if device_id is not None:
            where += " AND j.device_id = ?"
            parameters += (device_id,)
        with self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT j.*, (SELECT COUNT(*) FROM scout_captures c WHERE c.job_id = j.id)
                    AS capture_count
                FROM scout_jobs j WHERE {where}
                """,
                parameters,
            ).fetchone()
        if row is None:
            raise ScoutJobNotFound(job_id)
        return self._job_from_row(row)

    def list_jobs(self, device_id: str, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit), 1), 100)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT j.*, (SELECT COUNT(*) FROM scout_captures c WHERE c.job_id = j.id)
                    AS capture_count
                FROM scout_jobs j
                WHERE j.device_id = ?
                ORDER BY j.created_at DESC LIMIT ?
                """,
                (device_id, safe_limit),
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def count_outstanding_jobs(self, device_id: str) -> int:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS value FROM scout_jobs
                WHERE device_id = ? AND status IN ('QUEUED', 'RUNNING', 'RETRY_WAIT')
                """,
                (device_id,),
            ).fetchone()
        return int(row["value"])

    def list_captures(self, job_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM scout_captures WHERE job_id = ? ORDER BY ordinal, id",
                (job_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "job_id": row["job_id"],
                "client_capture_id": row["client_capture_id"],
                "ordinal": int(row["ordinal"]),
                "filename": row["filename"],
                "view_code": row["view_code"],
                "mime_type": row["mime_type"],
                "sha256": row["sha256"],
                "byte_count": int(row["byte_count"]),
                "width": int(row["width"]),
                "height": int(row["height"]),
                "path": row["path"],
                "captured_at": row["captured_at"],
                "device_quality": json.loads(row["device_quality_json"])
                if row["device_quality_json"]
                else None,
                "server_quality": json.loads(row["server_quality_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def events(self, job_id: str, device_id: str) -> list[dict[str, Any]]:
        self.get_job(job_id, device_id=device_id)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM scout_job_events WHERE job_id = ? ORDER BY seq",
                (job_id,),
            ).fetchall()
        return [
            {
                "seq": int(row["seq"]),
                "event_type": row["event_type"],
                "stage": row["stage"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _model_attempt_events(
        connection: sqlite3.Connection, job_id: str
    ) -> list[tuple[str, dict[str, Any]]]:
        rows = connection.execute(
            """
            SELECT event_type, payload_json FROM scout_job_events
            WHERE job_id = ?
              AND event_type IN ('MODEL_ATTEMPT_STARTED', 'MODEL_ATTEMPT_RECORDED')
            ORDER BY seq
            """,
            (job_id,),
        ).fetchall()
        return [
            (row["event_type"], json.loads(row["payload_json"])) for row in rows
        ]

    def begin_model_attempt(
        self,
        job_id: str,
        context: dict[str, Any],
        *,
        max_attempts: int,
    ) -> int | None:
        """Atomically reserve one bounded model call before any external request."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status, attempt, attempt_base FROM scout_jobs WHERE id = ?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    raise ScoutJobNotFound(job_id)
                if row["status"] != "RUNNING":
                    raise ScoutConflict(
                        "model attempts can only begin for a running job"
                    )
                if int(row["attempt"]) - int(row["attempt_base"]) >= max_attempts:
                    connection.execute("COMMIT")
                    return None
                attempt = int(row["attempt"]) + 1
                cursor = connection.execute(
                    """
                    UPDATE scout_jobs SET attempt = ?, updated_at = ?
                    WHERE id = ? AND status = 'RUNNING' AND attempt = ?
                    """,
                    (attempt, utc_now(), job_id, int(row["attempt"])),
                )
                if cursor.rowcount != 1:
                    raise ScoutConflict("model attempt reservation lost its race")
                self._append_event(
                    connection,
                    job_id,
                    "MODEL_ATTEMPT_STARTED",
                    "MULTIMODAL_OBSERVATION",
                    {**context, "attempt": attempt, "outcome_state": "STARTED"},
                )
                connection.execute("COMMIT")
                return attempt
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def record_model_attempt(
        self,
        job_id: str,
        proof: dict[str, Any],
        *,
        retry_delay_seconds: float | None = None,
    ) -> None:
        """Append an outcome and any retry transition in one transaction."""
        attempt = proof.get("attempt")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            raise ValueError("model attempt proof requires a positive attempt")
        if retry_delay_seconds is not None:
            if proof.get("available") is True:
                raise ValueError("a successful model attempt cannot schedule a retry")
            if retry_delay_seconds <= 0:
                raise ValueError("retry_delay_seconds must be positive")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status, attempt FROM scout_jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    raise ScoutJobNotFound(job_id)
                if row["status"] != "RUNNING":
                    raise ScoutConflict(
                        "model attempts can only be recorded for a running job"
                    )
                if int(row["attempt"]) != attempt:
                    raise ScoutConflict("model attempt proof does not match reservation")
                events = self._model_attempt_events(connection, job_id)
                started = any(
                    event_type == "MODEL_ATTEMPT_STARTED"
                    and payload.get("attempt") == attempt
                    for event_type, payload in events
                )
                recorded = any(
                    event_type == "MODEL_ATTEMPT_RECORDED"
                    and payload.get("attempt") == attempt
                    for event_type, payload in events
                )
                if not started:
                    raise ScoutConflict("model attempt was not reserved")
                if recorded:
                    raise ScoutConflict("model attempt outcome is already recorded")
                self._append_event(
                    connection,
                    job_id,
                    "MODEL_ATTEMPT_RECORDED",
                    "MULTIMODAL_OBSERVATION",
                    {**proof, "outcome_state": "RECORDED"},
                )
                if retry_delay_seconds is not None:
                    delay = min(max(float(retry_delay_seconds), 0.05), 300.0)
                    now_value = datetime.now(timezone.utc)
                    now = now_value.isoformat(timespec="milliseconds")
                    next_attempt_at = (
                        now_value + timedelta(seconds=delay)
                    ).isoformat(timespec="milliseconds")
                    cursor = connection.execute(
                        """
                        UPDATE scout_jobs
                        SET status = 'RETRY_WAIT', stage = 'MODEL_RETRY_WAIT',
                            error_code = 'MODEL_REQUEST_RETRY', error_detail = ?,
                            next_attempt_at = ?, updated_at = ?
                        WHERE id = ? AND status = 'RUNNING'
                        """,
                        (
                            str(proof.get("error") or "model request unavailable")[:300],
                            next_attempt_at,
                            now,
                            job_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ScoutConflict("model retry transition lost its race")
                    self._append_event(
                        connection,
                        job_id,
                        "MODEL_RETRY_SCHEDULED",
                        "MODEL_RETRY_WAIT",
                        {
                            "error_code": "MODEL_REQUEST_RETRY",
                            "next_attempt_at": next_attempt_at,
                        },
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def model_attempts(self, job_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM scout_job_events
                WHERE job_id = ? AND event_type = 'MODEL_ATTEMPT_RECORDED'
                ORDER BY seq
                """,
                (job_id,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def recover_incomplete_jobs(
        self,
        *,
        max_attempts: int = 3,
        retry_base_seconds: float = 0.0,
    ) -> int:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retry_base_seconds < 0:
            raise ValueError("retry_base_seconds cannot be negative")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                now_value = datetime.now(timezone.utc)
                now = now_value.isoformat(timespec="milliseconds")
                running = connection.execute(
                    """
                    SELECT id, attempt, attempt_base FROM scout_jobs
                    WHERE status = 'RUNNING'
                    """
                ).fetchall()
                for row in running:
                    attempt = int(row["attempt"])
                    events = self._model_attempt_events(connection, row["id"])
                    started_payload = next(
                        (
                            payload
                            for event_type, payload in reversed(events)
                            if event_type == "MODEL_ATTEMPT_STARTED"
                            and payload.get("attempt") == attempt
                        ),
                        None,
                    )
                    recorded_payload = next(
                        (
                            payload
                            for event_type, payload in reversed(events)
                            if event_type == "MODEL_ATTEMPT_RECORDED"
                            and payload.get("attempt") == attempt
                        ),
                        None,
                    )
                    if attempt > 0 and started_payload is not None and recorded_payload is None:
                        recovery_proof = {
                            key: value
                            for key, value in started_payload.items()
                            if key != "outcome_state"
                        }
                        recovery_proof.update(
                            {
                                "available": False,
                                "error": "OutcomeUnknownAfterRestart",
                                "outcome_state": "UNKNOWN_AFTER_RESTART",
                                "recovered_at": now,
                            }
                        )
                        self._append_event(
                            connection,
                            row["id"],
                            "MODEL_ATTEMPT_RECORDED",
                            "MULTIMODAL_OBSERVATION",
                            recovery_proof,
                        )
                        recorded_payload = recovery_proof

                    cycle_attempt = attempt - int(row["attempt_base"])
                    needs_backoff = (
                        recorded_payload is not None
                        and recorded_payload.get("available") is not True
                        and cycle_attempt < max_attempts
                        and retry_base_seconds > 0
                    )
                    if needs_backoff:
                        delay = min(
                            retry_base_seconds * (2 ** max(cycle_attempt - 1, 0)),
                            300.0,
                        )
                        next_attempt_at = (
                            now_value + timedelta(seconds=max(delay, 0.05))
                        ).isoformat(timespec="milliseconds")
                        connection.execute(
                            """
                            UPDATE scout_jobs
                            SET status = 'RETRY_WAIT', stage = 'MODEL_RETRY_WAIT',
                                error_code = 'MODEL_REQUEST_RETRY', error_detail = ?,
                                next_attempt_at = ?, updated_at = ?
                            WHERE id = ? AND status = 'RUNNING'
                            """,
                            (
                                str(
                                    recorded_payload.get("error")
                                    or "model request unavailable"
                                )[:300],
                                next_attempt_at,
                                now,
                                row["id"],
                            ),
                        )
                        self._append_event(
                            connection,
                            row["id"],
                            "MODEL_RETRY_SCHEDULED",
                            "MODEL_RETRY_WAIT",
                            {
                                "error_code": "MODEL_REQUEST_RETRY",
                                "next_attempt_at": next_attempt_at,
                                "reason": "PROCESS_RECOVERY",
                            },
                        )
                    else:
                        connection.execute(
                            """
                            UPDATE scout_jobs
                            SET status = 'QUEUED', stage = 'RECOVERED', updated_at = ?,
                                error_code = NULL, error_detail = NULL,
                                next_attempt_at = NULL
                            WHERE id = ? AND status = 'RUNNING'
                            """,
                            (now, row["id"]),
                        )
                connection.execute(
                    """
                    UPDATE scout_jobs
                    SET status = 'QUEUED', stage = 'RECOVERED', updated_at = ?,
                        error_code = NULL, error_detail = NULL
                    WHERE status = 'RETRY_WAIT' AND next_attempt_at IS NULL
                    """,
                    (now,),
                )
                connection.execute("COMMIT")
                return len(running)
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def claim_next_job(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                now = utc_now()
                connection.execute(
                    """
                    UPDATE scout_jobs
                    SET status = 'QUEUED', stage = 'RETRY_READY', updated_at = ?,
                        next_attempt_at = NULL
                    WHERE status = 'RETRY_WAIT' AND next_attempt_at <= ?
                    """,
                    (now, now),
                )
                row = connection.execute(
                    """
                    SELECT id FROM scout_jobs
                    WHERE status = 'QUEUED'
                    ORDER BY created_at LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return None
                connection.execute(
                    """
                    UPDATE scout_jobs
                    SET status = 'RUNNING', stage = 'QUALITY_CHECK',
                        started_at = COALESCE(started_at, ?),
                        updated_at = ?, error_code = NULL, error_detail = NULL,
                        next_attempt_at = NULL
                    WHERE id = ? AND status = 'QUEUED'
                    """,
                    (now, now, row["id"]),
                )
                self._append_event(
                    connection, row["id"], "JOB_STARTED", "QUALITY_CHECK"
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get_job(row["id"])

    def defer_job(
        self, job_id: str, error_code: str, detail: str, delay_seconds: float
    ) -> None:
        delay = min(max(float(delay_seconds), 0.05), 300.0)
        now_value = datetime.now(timezone.utc)
        now = now_value.isoformat(timespec="milliseconds")
        next_attempt_at = (now_value + timedelta(seconds=delay)).isoformat(
            timespec="milliseconds"
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE scout_jobs
                    SET status = 'RETRY_WAIT', stage = 'MODEL_RETRY_WAIT',
                        error_code = ?, error_detail = ?, next_attempt_at = ?,
                        updated_at = ?
                    WHERE id = ? AND status = 'RUNNING'
                    """,
                    (
                        error_code[:80],
                        detail[:300],
                        next_attempt_at,
                        now,
                        job_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ScoutConflict("only a running job can be deferred")
                self._append_event(
                    connection,
                    job_id,
                    "MODEL_RETRY_SCHEDULED",
                    "MODEL_RETRY_WAIT",
                    {
                        "error_code": error_code[:80],
                        "next_attempt_at": next_attempt_at,
                    },
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def retry_model_unavailable_job(
        self,
        job_id: str,
        device_id: str,
        *,
        max_outstanding_jobs: int,
        cooldown_seconds: float,
    ) -> dict[str, Any]:
        if max_outstanding_jobs < 1:
            raise ValueError("max_outstanding_jobs must be positive")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")
        now_value = datetime.now(timezone.utc)
        now = now_value.isoformat(timespec="milliseconds")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                job = connection.execute(
                    "SELECT status FROM scout_jobs WHERE id = ? AND device_id = ?",
                    (job_id, device_id),
                ).fetchone()
                if job is None:
                    raise ScoutJobNotFound(job_id)
                if job["status"] != "MODEL_UNAVAILABLE":
                    raise ScoutConflict(
                        "only a MODEL_UNAVAILABLE job can be retried in place"
                    )

                outstanding = connection.execute(
                    """
                    SELECT COUNT(*) AS value FROM scout_jobs
                    WHERE device_id = ?
                      AND status IN ('QUEUED', 'RUNNING', 'RETRY_WAIT')
                    """,
                    (device_id,),
                ).fetchone()
                if int(outstanding["value"]) >= max_outstanding_jobs:
                    raise ScoutCapacityError(
                        "Scout device has too many outstanding jobs"
                    )

                latest_retry = connection.execute(
                    """
                    SELECT e.created_at
                    FROM scout_job_events e
                    JOIN scout_jobs j ON j.id = e.job_id
                    WHERE j.device_id = ?
                      AND e.event_type = 'MODEL_RETRY_REQUESTED'
                    ORDER BY e.seq DESC
                    LIMIT 1
                    """,
                    (device_id,),
                ).fetchone()
                if latest_retry is not None and cooldown_seconds > 0:
                    last_retry_at = datetime.fromisoformat(latest_retry["created_at"])
                    elapsed = (now_value - last_retry_at).total_seconds()
                    if elapsed < cooldown_seconds:
                        remaining = max(cooldown_seconds - elapsed, 0.0)
                        raise ScoutCapacityError(
                            "Scout manual model retry cooldown is active "
                            f"({remaining:.1f}s remaining)"
                        )

                cursor = connection.execute(
                    """
                    UPDATE scout_jobs
                    SET status = 'QUEUED', stage = 'MODEL_RETRY_REQUESTED',
                        attempt_base = attempt, result_json = NULL, error_code = NULL,
                        error_detail = NULL, next_attempt_at = NULL,
                        updated_at = ?, completed_at = NULL
                    WHERE id = ? AND device_id = ? AND status = 'MODEL_UNAVAILABLE'
                    """,
                    (now, job_id, device_id),
                )
                if cursor.rowcount != 1:
                    raise ScoutConflict(
                        "only a MODEL_UNAVAILABLE job can be retried in place"
                    )
                self._append_event(
                    connection,
                    job_id,
                    "MODEL_RETRY_REQUESTED",
                    "MODEL_RETRY_REQUESTED",
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get_job(job_id, device_id=device_id)

    def update_stage(
        self, job_id: str, stage: str, payload: dict[str, Any] | None = None
    ) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "UPDATE scout_jobs SET stage = ?, updated_at = ? WHERE id = ?",
                    (stage, utc_now(), job_id),
                )
                self._append_event(connection, job_id, "STAGE_CHANGED", stage, payload)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def complete_job(
        self, job_id: str, status: str, result: dict[str, Any]
    ) -> None:
        if status not in TERMINAL_STATUSES - {"FAILED", "CANCELLED"}:
            raise ValueError("invalid successful terminal Scout status")
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE scout_jobs
                    SET status = ?, stage = 'COMPLETE', result_json = ?,
                        error_code = NULL, error_detail = NULL,
                        next_attempt_at = NULL, updated_at = ?, completed_at = ?
                    WHERE id = ? AND status = 'RUNNING'
                    """,
                    (status, canonical_json(result), now, now, job_id),
                )
                if cursor.rowcount != 1:
                    raise ScoutConflict("Scout terminal transition is no longer allowed")
                self._append_event(
                    connection,
                    job_id,
                    "JOB_COMPLETED",
                    "COMPLETE",
                    {"status": status, "result_sha256": result.get("result_sha256")},
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
    def fail_job(self, job_id: str, error_code: str, detail: str) -> None:
        now = utc_now()
        safe_detail = detail[:300]
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE scout_jobs
                    SET status = 'FAILED', stage = 'COMPLETE', error_code = ?,
                        error_detail = ?, next_attempt_at = NULL,
                        result_json = NULL, updated_at = ?, completed_at = ?
                    WHERE id = ? AND status = 'RUNNING'
                    """,
                    (error_code[:80], safe_detail, now, now, job_id),
                )
                if cursor.rowcount != 1:
                    raise ScoutConflict("Scout failure transition is no longer allowed")
                self._append_event(
                    connection,
                    job_id,
                    "JOB_FAILED",
                    "COMPLETE",
                    {"error_code": error_code[:80]},
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def cancel_job(self, job_id: str, device_id: str) -> dict[str, Any]:
        self.get_job(job_id, device_id=device_id)
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE scout_jobs
                    SET status = 'CANCELLED', stage = 'COMPLETE',
                        next_attempt_at = NULL, updated_at = ?, completed_at = ?
                    WHERE id = ? AND device_id = ?
                      AND status IN ('QUEUED', 'RETRY_WAIT')
                    """,
                    (now, now, job_id, device_id),
                )
                if cursor.rowcount != 1:
                    raise ScoutConflict("only queued or retry-wait jobs can be cancelled")
                self._append_event(
                    connection, job_id, "JOB_CANCELLED", "COMPLETE"
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get_job(job_id, device_id=device_id)
