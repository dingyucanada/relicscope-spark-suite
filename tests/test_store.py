from __future__ import annotations

import json
import sqlite3

import pytest

from app.store import SessionStore


def _state(session_id: str):
    return {
        "id": session_id,
        "artifact": {"name": "测试器物"},
        "claim": {"period": "待核验"},
        "protocol": {"id": "P01"},
        "demo_data": True,
        "counter": 0,
    }


def test_atomic_update_version_and_audit_chain(tmp_path):
    store = SessionStore(tmp_path / "test.sqlite3")
    store.initialize()
    created = store.create_session(_state("RS-STORE-1"))
    assert created["version"] == 1
    assert len(created["latest_audit_hash"]) == 64

    def increment(state):
        state["counter"] += 1
        return state, {"counter": state["counter"]}

    updated = store.atomic_update("RS-STORE-1", "COUNTER_INCREMENTED", increment)
    assert updated["version"] == 2
    assert updated["counter"] == 1
    details = store.verify_audit_chain_details("RS-STORE-1")
    assert details["valid"] is True
    assert details["event_count"] == 2

    def invalid(state):
        state["counter"] = 999
        raise ValueError("reject")

    with pytest.raises(ValueError):
        store.atomic_update("RS-STORE-1", "SHOULD_NOT_EXIST", invalid)
    unchanged = store.get_session("RS-STORE-1")
    assert unchanged["version"] == 2
    assert unchanged["counter"] == 1


def test_audit_tamper_is_located(tmp_path):
    db_path = tmp_path / "tamper.sqlite3"
    store = SessionStore(db_path)
    store.initialize()
    store.create_session(_state("RS-STORE-2"))
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE audit_events SET payload_json = ? WHERE session_id = ?",
            ('{"tampered":true}', "RS-STORE-2"),
        )
    details = store.verify_audit_chain_details("RS-STORE-2")
    assert details["valid"] is False
    assert details["failure_seq"] is not None
    assert details["reason"] == "event hash mismatch"


def test_audit_chain_detects_a_truncated_tail(tmp_path):
    db_path = tmp_path / "truncated.sqlite3"
    store = SessionStore(db_path)
    store.initialize()
    store.create_session(_state("RS-STORE-TRUNCATED"))
    store.atomic_update(
        "RS-STORE-TRUNCATED",
        "COUNTER_INCREMENTED",
        lambda state: ({**state, "counter": 1}, {"counter": 1}),
    )
    expected_tail = store.get_session("RS-STORE-TRUNCATED")["latest_audit_hash"]
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "DELETE FROM audit_events WHERE seq = "
            "(SELECT MAX(seq) FROM audit_events WHERE session_id = ?)",
            ("RS-STORE-TRUNCATED",),
        )

    details = store.verify_audit_chain_details("RS-STORE-TRUNCATED")

    assert details["valid"] is False
    assert details["reason"] == "session state audit tail mismatch"
    assert details["state_tail_hash"] == expected_tail
    assert details["latest_hash"] != expected_tail


def test_audit_chain_detects_a_tampered_session_tail_pointer(tmp_path):
    db_path = tmp_path / "state-tail.sqlite3"
    store = SessionStore(db_path)
    store.initialize()
    store.create_session(_state("RS-STORE-STATE-TAIL"))
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT state_json FROM sessions WHERE id = ?", ("RS-STORE-STATE-TAIL",)
        ).fetchone()
        state = json.loads(row[0])
        state["latest_audit_hash"] = "0" * 64
        connection.execute(
            "UPDATE sessions SET state_json = ? WHERE id = ?",
            (json.dumps(state, ensure_ascii=False), "RS-STORE-STATE-TAIL"),
        )

    details = store.verify_audit_chain_details("RS-STORE-STATE-TAIL")

    assert details["valid"] is False
    assert details["reason"] == "session state audit tail mismatch"
    assert details["state_tail_hash"] == "0" * 64


def test_audit_chain_binds_and_detects_tampered_session_content(tmp_path):
    db_path = tmp_path / "state-content.sqlite3"
    store = SessionStore(db_path)
    store.initialize()
    store.create_session(_state("RS-STORE-STATE-CONTENT"))

    verified = store.verify_audit_chain_details("RS-STORE-STATE-CONTENT")
    assert verified["valid"] is True
    assert verified["state_integrity_bound"] is True
    assert verified["verification_strength"] == "AUDIT_CHAIN_AND_SESSION_STATE"
    assert len(verified["session_state_sha256"]) == 64

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT state_json FROM sessions WHERE id = ?",
            ("RS-STORE-STATE-CONTENT",),
        ).fetchone()
        state = json.loads(row[0])
        state["counter"] = 999
        connection.execute(
            "UPDATE sessions SET state_json = ? WHERE id = ?",
            (json.dumps(state, ensure_ascii=False), "RS-STORE-STATE-CONTENT"),
        )

    tampered = store.verify_audit_chain_details("RS-STORE-STATE-CONTENT")
    assert tampered["valid"] is False
    assert tampered["reason"] == "session state content hash mismatch"
    assert tampered["state_integrity_bound"] is True
    assert tampered["state_integrity_valid"] is False
