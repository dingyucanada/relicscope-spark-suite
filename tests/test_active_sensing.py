from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from app.schemas import ExecuteActionRequest
from app.services.active_sensing import (
    claim_action_for_execution,
    evaluate_action,
    plan_and_reserve,
    settle_and_gate,
)
from app.services.instruments import ReplayInstrumentAdapter


def _new_session(client):
    response = client.post(
        "/api/sessions",
        json={
            "artifact_name": "主动检测并发测试器物",
            "operator": "测试员",
            "institution": "RelicScope Demo Lab",
            "claim": {
                "period": "清代",
                "kiln": "景德镇窑",
                "material": "青花瓷",
                "provenance_note": "来源待核验",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["session"]["id"]


def _claim(state):
    run_id = state["current_action_run_id"]
    state, audit = claim_action_for_execution(state, run_id)
    assert state["current_action_status"] == "EXECUTING"
    assert audit["decision"] == "EXECUTION_CLAIMED"
    return state, run_id


def test_p01_replay_sequence(active_state):
    adapter = ReplayInstrumentAdapter()

    state, first_plan = plan_and_reserve(active_state)
    assert first_plan["selected_action"]["id"] == "A2"
    assert first_plan["selected_action"]["utility"] == pytest.approx(0.1689)
    assert state["risk_budgets"]["R1"]["photochemical"]["reserved"] == 0.20
    state, first_run = _claim(state)

    raman = adapter.execute(first_plan["selected_action"], "raman_low_snr")
    raman["action_run_id"] = first_run
    state, first_execution = settle_and_gate(state, raman)
    assert first_execution["operation_order"] == [
        "DEVICE_EXECUTION",
        "PHYSICAL_RISK_SETTLEMENT",
        "QUALITY_GATE",
        "PROPOSITION_UPDATE_OR_HOLD",
    ]
    assert state["risk_budgets"]["R1"]["photochemical"]["used"] == 0.73
    assert state["risk_budgets"]["R1"]["photochemical"]["reserved"] == 0.0
    assert state["uncertainty"] == 0.85
    assert state["executions"][0]["quality_gate"]["passed"] is False
    assert "R1:RAMAN:STANDARD" in state["retry_blocked"]

    state, second_plan = plan_and_reserve(state)
    assert second_plan["selected_action"]["id"] == "A1"
    xrf = next(item for item in second_plan["evaluations"] if item["id"] == "A3")
    assert xrf["decision"] == "BLOCKED"
    assert any("超预算" in reason for reason in xrf["reasons"])
    raman_evaluation = next(
        item for item in second_plan["evaluations"] if item["id"] == "A2"
    )
    assert raman_evaluation["decision"] == "BLOCKED"

    state, second_run = _claim(state)
    hsi = adapter.execute(second_plan["selected_action"], "hsi_material_anomaly")
    hsi["action_run_id"] = second_run
    state, second_execution = settle_and_gate(state, hsi)
    assert second_execution["quality_gate"]["passed"] is True
    assert state["uncertainty"] == 0.48
    assert state["status"] == "complete"
    assert state["claim_consistency"] == "REVIEW_REQUIRED"
    assert state["risk_budgets"]["R1"]["photochemical"]["used"] == 0.78


def test_missing_telemetry_is_settled_conservatively(active_state):
    state, plan = plan_and_reserve(active_state)
    state, run_id = _claim(state)
    result = ReplayInstrumentAdapter().execute(
        plan["selected_action"], "raman_missing_telemetry"
    )
    result["action_run_id"] = run_id
    state, audit = settle_and_gate(state, result)
    budget = state["risk_budgets"]["R1"]["photochemical"]
    assert budget["used"] == 0.75
    assert budget["reserved"] == 0.0
    assert budget["locked"] is True
    assert audit["settlement"]["photochemical"]["settled"] == 0.20


def test_material_and_device_preconditions_are_hard_constraints(active_state):
    action = deepcopy(active_state["candidate_actions"][0])
    action["preconditions"]["material_allowed"] = False
    action["preconditions"]["device_ready"] = False
    evaluation = evaluate_action(active_state, action)
    assert evaluation["feasible"] is False
    assert evaluation["decision"] == "BLOCKED"
    assert len(evaluation["reasons"]) == 2


def test_second_plan_cannot_reserve_while_action_is_active(active_state):
    state, _ = plan_and_reserve(active_state)
    with pytest.raises(ValueError, match="already reserved"):
        plan_and_reserve(state)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("action_run_id", "RUN-STALE"),
        ("action_id", "A1"),
        ("region_id", "R2"),
        ("modality", "HSI"),
    ],
)
def test_settlement_rejects_mismatched_execution_identity(
    active_state, field, invalid_value
):
    state, plan = plan_and_reserve(active_state)
    state, run_id = _claim(state)
    result = ReplayInstrumentAdapter().execute(plan["selected_action"], "raman_low_snr")
    result["action_run_id"] = run_id
    result[field] = invalid_value
    budget_before = deepcopy(state["risk_budgets"])

    with pytest.raises(ValueError, match=field):
        settle_and_gate(state, result)

    assert state["risk_budgets"] == budget_before
    assert state["current_action_status"] == "EXECUTING"
    assert state["executions"] == []


class _BlockingCountingAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, action, profile_name=""):
        with self._lock:
            self.calls += 1
        self.started.set()
        if not self.release.wait(timeout=2.0):
            raise TimeoutError("test adapter release timed out")
        return ReplayInstrumentAdapter().execute(action, profile_name)


def test_concurrent_duplicate_execute_invokes_adapter_once_and_returns_result(
    api_client,
):
    session_id = _new_session(api_client)
    service = api_client.app.state.service
    adapter = _BlockingCountingAdapter()
    service.instrument_adapter = adapter
    planned = service.plan(session_id)["session"]
    run_id = planned["current_action_run_id"]
    request = ExecuteActionRequest(
        action_run_id=run_id,
        replay_profile="raman_low_snr",
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(service.execute, session_id, request)
        assert adapter.started.wait(timeout=1.0)
        duplicate = pool.submit(service.execute, session_id, request)
        adapter.release.set()
        winner_envelope = winner.result(timeout=3.0)
        duplicate_envelope = duplicate.result(timeout=3.0)

    assert adapter.calls == 1
    assert len(winner_envelope["session"]["executions"]) == 1
    assert len(duplicate_envelope["session"]["executions"]) == 1
    assert (
        winner_envelope["session"]["version"]
        == duplicate_envelope["session"]["version"]
    )
    winner_result = winner_envelope["session"]["executions"][0]["result"]
    duplicate_result = duplicate_envelope["session"]["executions"][0]["result"]
    assert duplicate_result["result_hash"] == winner_result["result_hash"]
    events = service.store.get_audit_events(session_id)
    assert (
        sum(event["event_type"] == "ACTION_EXECUTION_CLAIMED" for event in events) == 1
    )
    assert (
        sum(event["event_type"] == "ACTION_EXECUTED_AND_SETTLED" for event in events)
        == 1
    )


class _RaisingAdapter:
    def __init__(self, exception: Exception) -> None:
        self.exception = exception
        self.calls = 0

    def execute(self, action, profile_name=""):
        self.calls += 1
        raise self.exception


@pytest.mark.parametrize(
    ("exception", "expected_category"),
    [
        (TimeoutError("instrument timeout"), "ADAPTER_TIMEOUT"),
        (RuntimeError("instrument disconnected"), "ADAPTER_EXCEPTION"),
    ],
)
def test_adapter_failure_is_conservatively_settled_and_locks_channel(
    api_client, exception, expected_category
):
    session_id = _new_session(api_client)
    service = api_client.app.state.service
    adapter = _RaisingAdapter(exception)
    service.instrument_adapter = adapter
    planned = service.plan(session_id)["session"]
    run_id = planned["current_action_run_id"]

    envelope = service.execute(
        session_id,
        ExecuteActionRequest(action_run_id=run_id, replay_profile="raman_low_snr"),
    )

    state = envelope["session"]
    assert adapter.calls == 1
    assert state["current_action_id"] is None
    assert state["current_action_run_id"] is None
    assert state["current_action_status"] is None
    assert state["execution_claim"] is None
    budget = state["risk_budgets"]["R1"]["photochemical"]
    assert budget["used"] == pytest.approx(0.75)
    assert budget["reserved"] == 0.0
    assert budget["locked"] is True
    assert state["uncertainty"] == 0.85
    execution = state["executions"][0]
    assert execution["quality_gate"]["passed"] is False
    assert execution["settlement"]["photochemical"]["telemetry_valid"] is False
    assert execution["result"]["error_category"] == expected_category
    assert execution["result"]["execution_status"] == "FAILED_CONSERVATIVE_SETTLEMENT"
    assert len(execution["result"]["result_hash"]) == 64

    evidence_nodes = [
        node for node in state["evidence_graph"]["nodes"] if node["type"] == "evidence"
    ]
    assert evidence_nodes[-1]["meta"]["demo_data"] is True
    assert evidence_nodes[-1]["meta"]["source_category"] == "DEMO/SYNTHETIC"
    settlement_event = service.store.get_audit_events(session_id)[-1]
    assert settlement_event["payload"]["source_category"] == "DEMO/SYNTHETIC"
    assert settlement_event["payload"]["protocol"] == "P01-ACTIVE-SENSING-DEMO-V1"
    assert (
        settlement_event["payload"]["result_hash"] == execution["result"]["result_hash"]
    )


def test_malformed_adapter_identity_is_conservatively_settled(api_client):
    class WrongRegionAdapter:
        def execute(self, action, profile_name=""):
            result = ReplayInstrumentAdapter().execute(action, profile_name)
            result["region_id"] = "R-STALE"
            return result

    session_id = _new_session(api_client)
    service = api_client.app.state.service
    service.instrument_adapter = WrongRegionAdapter()
    planned = service.plan(session_id)["session"]
    envelope = service.execute(
        session_id,
        ExecuteActionRequest(
            action_run_id=planned["current_action_run_id"],
            replay_profile="raman_low_snr",
        ),
    )

    execution = envelope["session"]["executions"][0]
    assert execution["action"]["region_id"] == "R1"
    assert execution["result"]["region_id"] == "R1"
    assert execution["result"]["error_category"] == "ADAPTER_CONTRACT_VIOLATION"
    assert execution["settlement"]["photochemical"]["locked"] is True
