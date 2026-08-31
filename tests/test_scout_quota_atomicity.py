from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.scout.store import ScoutCapacityError, ScoutStore


def _store(tmp_path) -> tuple[ScoutStore, str]:
    store = ScoutStore(tmp_path / "scout-quota.sqlite3")
    store.initialize()
    device_id = store.enroll_device("Concurrent Scout")["device_id"]
    return store, device_id


def _create(
    store: ScoutStore,
    device_id: str,
    client_job_id: str,
    payload_sha256: str,
    *,
    limit: int,
):
    return store.create_job(
        device_id=device_id,
        client_job_id=client_job_id,
        payload_sha256=payload_sha256,
        request={"client_job_id": client_job_id},
        captures=[],
        max_outstanding_jobs=limit,
    )


def _terminal_model_job(
    store: ScoutStore, device_id: str, sequence: int
) -> dict:
    job, created = _create(
        store,
        device_id,
        f"terminal-{sequence}",
        f"{sequence:064x}",
        limit=100,
    )
    assert created is True
    claimed = store.claim_next_job()
    assert claimed is not None and claimed["id"] == job["id"]
    store.complete_job(
        job["id"],
        "MODEL_UNAVAILABLE",
        {"schema_version": "quota-test-result"},
    )
    return store.get_job(job["id"], device_id=device_id)


def test_concurrent_distinct_creates_cannot_oversubscribe_device_quota(tmp_path):
    store, device_id = _store(tmp_path)
    barrier = Barrier(2)

    def submit(sequence: int):
        barrier.wait(timeout=5)
        try:
            _, created = _create(
                store,
                device_id,
                f"concurrent-{sequence}",
                f"{sequence:064x}",
                limit=1,
            )
            return "created" if created else "replayed"
        except ScoutCapacityError:
            return "capacity"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(submit, (1, 2)))

    assert sorted(outcomes) == ["capacity", "created"]
    assert store.count_outstanding_jobs(device_id) == 1


def test_concurrent_byte_identical_replay_remains_idempotent_at_quota(tmp_path):
    store, device_id = _store(tmp_path)
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=5)
        job, created = _create(
            store,
            device_id,
            "same-client-job",
            "a" * 64,
            limit=1,
        )
        return job["id"], created

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: submit(), range(2)))

    assert {job_id for job_id, _ in outcomes} == {outcomes[0][0]}
    assert sorted(created for _, created in outcomes) == [False, True]
    assert store.count_outstanding_jobs(device_id) == 1


def test_concurrent_manual_retries_share_the_same_atomic_quota(tmp_path):
    store, device_id = _store(tmp_path)
    jobs = [
        _terminal_model_job(store, device_id, 1),
        _terminal_model_job(store, device_id, 2),
    ]
    barrier = Barrier(2)

    def retry(job: dict):
        barrier.wait(timeout=5)
        try:
            store.retry_model_unavailable_job(
                job["id"],
                device_id,
                max_outstanding_jobs=1,
                cooldown_seconds=0,
            )
            return "queued"
        except ScoutCapacityError:
            return "capacity"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(retry, jobs))

    assert sorted(outcomes) == ["capacity", "queued"]
    assert store.count_outstanding_jobs(device_id) == 1


def test_manual_retry_cooldown_is_device_wide(tmp_path):
    store, device_id = _store(tmp_path)
    first = _terminal_model_job(store, device_id, 1)
    second = _terminal_model_job(store, device_id, 2)

    store.retry_model_unavailable_job(
        first["id"],
        device_id,
        max_outstanding_jobs=10,
        cooldown_seconds=60,
    )
    claimed = store.claim_next_job()
    assert claimed is not None and claimed["id"] == first["id"]
    store.complete_job(
        first["id"],
        "MODEL_UNAVAILABLE",
        {"schema_version": "quota-test-result"},
    )

    with pytest.raises(ScoutCapacityError, match="cooldown"):
        store.retry_model_unavailable_job(
            second["id"],
            device_id,
            max_outstanding_jobs=10,
            cooldown_seconds=60,
        )

    assert store.get_job(second["id"], device_id=device_id)["status"] == (
        "MODEL_UNAVAILABLE"
    )
    retry_events = [
        event
        for event in store.events(first["id"], device_id)
        if event["event_type"] == "MODEL_RETRY_REQUESTED"
    ]
    assert len(retry_events) == 1
