from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Tuple


StateUpdater = Callable[[Dict[str, Any]], Tuple[Dict[str, Any], Dict[str, Any]]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def session_state_sha256(state: Dict[str, Any]) -> str:
    """Hash session content without the audit-tail pointer.

    The pointer is excluded because it would otherwise create a circular hash:
    the audit event binds the state, while the state stores that event's hash.
    """

    payload = deepcopy(state)
    payload.pop("latest_audit_hash", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class SessionNotFound(KeyError):
    pass


class SessionStore:
    """SQLite store with atomic state transitions and a chained audit log."""

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
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_audit_session_seq
                ON audit_events(session_id, seq);

                CREATE TABLE IF NOT EXISTS raw_files (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );
                """
            )

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        previous = connection.execute(
            "SELECT event_hash FROM audit_events WHERE session_id = ? ORDER BY seq DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        prev_hash = previous["event_hash"] if previous else "GENESIS"
        created_at = utc_now()
        next_seq_row = connection.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM audit_events"
        ).fetchone()
        seq = int(next_seq_row["next_seq"])
        body = {
            "seq": seq,
            "session_id": session_id,
            "event_type": event_type,
            "payload": payload,
            "prev_hash": prev_hash,
            "created_at": created_at,
        }
        event_hash = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO audit_events
                (seq, session_id, event_type, payload_json, prev_hash, event_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                seq,
                session_id,
                event_type,
                canonical_json(payload),
                prev_hash,
                event_hash,
                created_at,
            ),
        )
        return {**body, "event_hash": event_hash}

    @staticmethod
    def _bind_state_integrity(
        payload: Dict[str, Any], state: Dict[str, Any]
    ) -> Dict[str, Any]:
        bound_payload = deepcopy(payload)
        bound_payload["_integrity"] = {
            "algorithm": "SHA-256",
            "canonicalization": "sorted compact JSON UTF-8",
            "state_version": int(state["version"]),
            "session_state_sha256": session_state_sha256(state),
        }
        return bound_payload

    def create_session(self, state: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_now()
        state["version"] = 1
        state["created_at"] = state.get("created_at", now)
        state["updated_at"] = state.get("updated_at", now)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO sessions(id, state_json, version, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
                    (state["id"], canonical_json(state), now, now),
                )
                audit_payload = self._bind_state_integrity(
                    {
                        "artifact_name": state["artifact"]["name"],
                        "claim": state["claim"],
                        "protocol": state["protocol"],
                        "demo_data": state["demo_data"],
                    },
                    state,
                )
                audit_event = self._append_audit(
                    connection,
                    state["id"],
                    "SESSION_CREATED",
                    audit_payload,
                )
                state["latest_audit_hash"] = audit_event["event_hash"]
                connection.execute(
                    "UPDATE sessions SET state_json = ? WHERE id = ?",
                    (canonical_json(state), state["id"]),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return state

    def _write_updated_state(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        state: Dict[str, Any],
        previous_version: int,
        event_type: str,
        audit_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        version = previous_version + 1
        state["version"] = version
        state["updated_at"] = utc_now()
        connection.execute(
            "UPDATE sessions SET state_json = ?, version = ?, updated_at = ? WHERE id = ?",
            (canonical_json(state), version, state["updated_at"], session_id),
        )
        bound_payload = self._bind_state_integrity(audit_payload, state)
        audit_event = self._append_audit(
            connection, session_id, event_type, bound_payload
        )
        state["latest_audit_hash"] = audit_event["event_hash"]
        connection.execute(
            "UPDATE sessions SET state_json = ? WHERE id = ?",
            (canonical_json(state), session_id),
        )
        return state

    def get_session(self, session_id: str) -> Dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT state_json, version FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise SessionNotFound(session_id)
            state = json.loads(row["state_json"])
            state["version"] = int(row["version"])
            return state

    def atomic_update(
        self,
        session_id: str,
        event_type: str,
        updater: StateUpdater,
    ) -> Dict[str, Any]:
        """Run a state transition and its audit event in one SQLite transaction."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT state_json, version FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                if row is None:
                    raise SessionNotFound(session_id)
                state = json.loads(row["state_json"])
                updated_state, audit_payload = updater(state)
                updated_state = self._write_updated_state(
                    connection,
                    session_id,
                    updated_state,
                    int(row["version"]),
                    event_type,
                    audit_payload,
                )
                connection.execute("COMMIT")
                return updated_state
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def atomic_register_raw_file(
        self,
        session_id: str,
        file_record: Dict[str, Any],
        updater: StateUpdater,
        *,
        event_type: str = "RAW_FILE_ACCEPTED",
    ) -> Dict[str, Any]:
        """Register a raw file, session state and audit event atomically."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT state_json, version FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                if row is None:
                    raise SessionNotFound(session_id)
                state = json.loads(row["state_json"])
                updated_state, audit_payload = updater(state)
                connection.execute(
                    """
                    INSERT INTO raw_files
                        (id, session_id, filename, mime_type, sha256, path, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_record["id"],
                        session_id,
                        file_record["filename"],
                        file_record["mime_type"],
                        file_record["sha256"],
                        file_record["path"],
                        canonical_json(file_record["metadata"]),
                        file_record["created_at"],
                    ),
                )
                updated_state = self._write_updated_state(
                    connection,
                    session_id,
                    updated_state,
                    int(row["version"]),
                    event_type,
                    audit_payload,
                )
                connection.execute("COMMIT")
                return updated_state
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def atomic_register_raw_files(
        self,
        session_id: str,
        file_records: List[Dict[str, Any]],
        updater: StateUpdater,
        *,
        event_type: str,
    ) -> Dict[str, Any]:
        """Register a bounded group of related files in one state transition."""

        if not file_records:
            raise ValueError("at least one file record is required")
        identifiers = [str(item["id"]) for item in file_records]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("file record identifiers must be unique")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT state_json, version FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                if row is None:
                    raise SessionNotFound(session_id)
                state = json.loads(row["state_json"])
                updated_state, audit_payload = updater(state)
                for file_record in file_records:
                    connection.execute(
                        """
                        INSERT INTO raw_files
                            (id, session_id, filename, mime_type, sha256, path, metadata_json, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            file_record["id"],
                            session_id,
                            file_record["filename"],
                            file_record["mime_type"],
                            file_record["sha256"],
                            file_record["path"],
                            canonical_json(file_record["metadata"]),
                            file_record["created_at"],
                        ),
                    )
                updated_state = self._write_updated_state(
                    connection,
                    session_id,
                    updated_state,
                    int(row["version"]),
                    event_type,
                    audit_payload,
                )
                connection.execute("COMMIT")
                return updated_state
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def save_raw_file(
        self,
        file_id: str,
        session_id: str,
        filename: str,
        mime_type: str,
        sha256: str,
        path: Path,
        metadata: Dict[str, Any],
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO raw_files
                    (id, session_id, filename, mime_type, sha256, path, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    session_id,
                    filename,
                    mime_type,
                    sha256,
                    str(path),
                    canonical_json(metadata),
                    utc_now(),
                ),
            )

    def get_audit_events(self, session_id: str) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE session_id = ? ORDER BY seq", (session_id,)
            ).fetchall()
        return [
            {
                "seq": int(row["seq"]),
                "session_id": row["session_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "prev_hash": row["prev_hash"],
                "event_hash": row["event_hash"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def list_raw_files(self, session_id: str) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM raw_files WHERE session_id = ? ORDER BY created_at, id",
                (session_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "filename": row["filename"],
                "mime_type": row["mime_type"],
                "sha256": row["sha256"],
                "path": row["path"],
                "metadata": json.loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def verify_audit_chain(self, session_id: str) -> bool:
        return bool(self.verify_audit_chain_details(session_id)["valid"])

    def verify_audit_chain_details(self, session_id: str) -> Dict[str, Any]:
        state = self.get_session(session_id)
        state_tail_hash = state.get("latest_audit_hash")
        events = self.get_audit_events(session_id)
        previous_hash = "GENESIS"
        for event in events:
            if event["prev_hash"] != previous_hash:
                return {
                    "valid": False,
                    "event_count": len(events),
                    "failure_seq": event["seq"],
                    "reason": "previous hash mismatch",
                    "latest_hash": previous_hash,
                    "state_tail_hash": state_tail_hash,
                }
            body = {
                "seq": event["seq"],
                "session_id": event["session_id"],
                "event_type": event["event_type"],
                "payload": event["payload"],
                "prev_hash": event["prev_hash"],
                "created_at": event["created_at"],
            }
            calculated = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
            if calculated != event["event_hash"]:
                return {
                    "valid": False,
                    "event_count": len(events),
                    "failure_seq": event["seq"],
                    "reason": "event hash mismatch",
                    "latest_hash": previous_hash,
                    "state_tail_hash": state_tail_hash,
                }
            previous_hash = event["event_hash"]
        if not isinstance(state_tail_hash, str) or state_tail_hash != previous_hash:
            return {
                "valid": False,
                "event_count": len(events),
                "failure_seq": events[-1]["seq"] if events else None,
                "reason": "session state audit tail mismatch",
                "latest_hash": previous_hash,
                "state_tail_hash": state_tail_hash,
            }
        state_hash = session_state_sha256(state)
        latest_integrity = (
            events[-1].get("payload", {}).get("_integrity") if events else None
        )
        if isinstance(latest_integrity, dict):
            expected_state_hash = latest_integrity.get("session_state_sha256")
            expected_state_version = latest_integrity.get("state_version")
            if (
                expected_state_hash != state_hash
                or expected_state_version != int(state.get("version", 0))
            ):
                return {
                    "valid": False,
                    "event_count": len(events),
                    "failure_seq": events[-1]["seq"] if events else None,
                    "reason": "session state content hash mismatch",
                    "latest_hash": previous_hash,
                    "state_tail_hash": state_tail_hash,
                    "state_integrity_bound": True,
                    "state_integrity_valid": False,
                    "session_state_sha256": state_hash,
                    "expected_session_state_sha256": expected_state_hash,
                }
            state_integrity_bound = True
        else:
            # Legacy sessions remain readable. The next accepted update creates
            # a state-bound event and upgrades verification strength.
            state_integrity_bound = False
        return {
            "valid": True,
            "event_count": len(events),
            "failure_seq": None,
            "reason": "ok",
            "latest_hash": previous_hash,
            "state_tail_hash": state_tail_hash,
            "state_integrity_bound": state_integrity_bound,
            "state_integrity_valid": state_integrity_bound,
            "session_state_sha256": state_hash,
            "verification_strength": (
                "AUDIT_CHAIN_AND_SESSION_STATE"
                if state_integrity_bound
                else "AUDIT_CHAIN_ONLY_LEGACY"
            ),
        }
