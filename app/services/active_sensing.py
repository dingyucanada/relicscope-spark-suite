from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from .evidence import add_edge, add_node
from .instruments import quality_gate


ALPHA = 0.55
BETA = 0.20
GAMMA = 0.25
MIN_UTILITY = 0.10
MIN_INFORMATION_GAIN = 0.25
STOP_UNCERTAINTY = 0.50
EPSILON = 1e-9
ACTION_RESERVED = "RESERVED"
ACTION_EXECUTING = "EXECUTING"


class ActionAlreadySettled(RuntimeError):
    """Raised inside the claim transaction when the run is already durable."""


class ActionExecutionInProgress(RuntimeError):
    """Raised inside the claim transaction when another caller owns the run."""


DEFAULT_ACTIONS: List[Dict[str, Any]] = [
    {
        "id": "A1",
        "label": "HSI @ R1",
        "modality": "HSI",
        "region_id": "R1",
        "predicted_load": {"photochemical": 0.06},
        "pass_rate": 0.95,
        "expected_uncertainty": 0.48,
        "cost": 0.15,
        "retry_key": "R1:HSI:STANDARD",
        "default_replay_profile": "hsi_material_anomaly",
        "preconditions": {
            "material_allowed": True,
            "calibration_ready": True,
            "device_ready": True,
        },
    },
    {
        "id": "A2",
        "label": "Raman @ R1",
        "modality": "Raman",
        "region_id": "R1",
        "predicted_load": {"photochemical": 0.20},
        "pass_rate": 0.80,
        "expected_uncertainty": 0.10,
        "cost": 0.25,
        "retry_key": "R1:RAMAN:STANDARD",
        "default_replay_profile": "raman_low_snr",
        "preconditions": {
            "material_allowed": True,
            "calibration_ready": True,
            "device_ready": True,
        },
    },
    {
        "id": "A3",
        "label": "XRF @ R1",
        "modality": "XRF",
        "region_id": "R1",
        "predicted_load": {"ionizing": 0.65},
        "pass_rate": 0.90,
        "expected_uncertainty": 0.05,
        "cost": 0.30,
        "retry_key": "R1:XRF:STANDARD",
        "default_replay_profile": "",
        "preconditions": {
            "material_allowed": True,
            "calibration_ready": True,
            "device_ready": True,
        },
    },
    {
        "id": "A4",
        "label": "重复 UV @ R1",
        "modality": "UV",
        "region_id": "R1",
        "predicted_load": {"photochemical": 0.05},
        "pass_rate": 0.98,
        "expected_uncertainty": 0.75,
        "cost": 0.05,
        "retry_key": "R1:UV:REPEAT",
        "default_replay_profile": "uv_valid",
        "preconditions": {
            "material_allowed": True,
            "calibration_ready": True,
            "device_ready": True,
        },
    },
]


def default_budgets() -> Dict[str, Dict[str, Dict[str, Any]]]:
    return {
        "R1": {
            "photochemical": {
                "unit": "J/cm²",
                "limit": 1.0,
                "used": 0.55,
                "reserved": 0.0,
                "locked": False,
            },
            "ionizing": {
                "unit": "mGy",
                "limit": 2.0,
                "used": 1.50,
                "reserved": 0.0,
                "locked": False,
            },
        }
    }


def _budget_for(state: Dict[str, Any], region_id: str, channel: str) -> Dict[str, Any]:
    try:
        return state["risk_budgets"][region_id][channel]
    except KeyError as exc:
        raise ValueError(f"missing risk budget for {region_id}/{channel}") from exc


def evaluate_action(state: Dict[str, Any], action: Dict[str, Any]) -> Dict[str, Any]:
    current_uncertainty = float(state["uncertainty"])
    information_gain = float(action["pass_rate"]) * max(
        0.0, current_uncertainty - float(action["expected_uncertainty"])
    )
    blocked_retries = set(state.get("retry_blocked", []))
    hard_reasons: List[str] = []
    risk_ratios: List[float] = []

    if action["retry_key"] in blocked_retries:
        hard_reasons.append("同参数动作已因质量失败被抑制")

    preconditions = action.get("preconditions", {})
    if not preconditions.get("material_allowed", False):
        hard_reasons.append("材料安全禁忌不允许该动作")
    if not preconditions.get("calibration_ready", False):
        hard_reasons.append("必要校准未就绪")
    if not preconditions.get("device_ready", False):
        hard_reasons.append("设备安全状态未就绪")

    for channel, predicted_load in action["predicted_load"].items():
        budget = _budget_for(state, action["region_id"], channel)
        if budget.get("locked"):
            hard_reasons.append(f"{channel} 风险通道已锁定")
        projected = (
            float(budget["used"]) + float(budget["reserved"]) + float(predicted_load)
        )
        if projected > float(budget["limit"]) + EPSILON:
            hard_reasons.append(
                f"{channel} 超预算：{projected:.4f} > {float(budget['limit']):.4f} {budget['unit']}"
            )
        remaining = max(
            EPSILON,
            float(budget["limit"]) - float(budget["used"]) - float(budget["reserved"]),
        )
        risk_ratios.append(min(1.0, float(predicted_load) / remaining))

    risk_ratio = max(risk_ratios or [0.0])
    utility = (
        ALPHA * information_gain - BETA * float(action["cost"]) - GAMMA * risk_ratio
    )
    feasible = not hard_reasons
    passes_thresholds = (
        utility >= MIN_UTILITY and information_gain >= MIN_INFORMATION_GAIN
    )
    return {
        **deepcopy(action),
        "information_gain": round(information_gain, 4),
        "risk_ratio": round(risk_ratio, 4),
        "utility": round(utility, 4),
        "feasible": feasible,
        "passes_thresholds": passes_thresholds,
        "decision": (
            "BLOCKED"
            if not feasible
            else "ELIGIBLE"
            if passes_thresholds
            else "BELOW_THRESHOLD"
        ),
        "reasons": hard_reasons,
    }


def plan_and_reserve(state: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if state.get("current_action_id"):
        raise ValueError("an action is already reserved")
    if float(state["uncertainty"]) <= STOP_UNCERTAINTY:
        state["status"] = "complete"
        state["next_step"] = "生成可审计报告"
        return state, {
            "decision": "STOP",
            "reason": "关键命题不确定度已达到停止阈值",
            "uncertainty": state["uncertainty"],
        }

    evaluations = [
        evaluate_action(state, action) for action in state["candidate_actions"]
    ]
    eligible = [
        item for item in evaluations if item["feasible"] and item["passes_thresholds"]
    ]
    eligible.sort(key=lambda item: (item["utility"], -item["risk_ratio"]), reverse=True)
    state["last_plan"] = evaluations
    state.setdefault("plan_history", []).append(
        {
            "round": len(state.get("plan_history", [])) + 1,
            "uncertainty": state["uncertainty"],
            "evaluations": evaluations,
        }
    )

    if not eligible:
        state["status"] = "abstained"
        state["claim_consistency"] = "EVIDENCE_INSUFFICIENT"
        state["next_step"] = "专家复核或外部实验室升级"
        return state, {
            "decision": "ABSTAIN",
            "reason": "没有同时满足安全硬约束、信息价值和效用阈值的动作",
            "evaluations": evaluations,
        }

    selected = eligible[0]
    action_run_id = f"RUN-{uuid4().hex[:16].upper()}"
    for channel, predicted_load in selected["predicted_load"].items():
        budget = _budget_for(state, selected["region_id"], channel)
        budget["reserved"] = round(float(budget["reserved"]) + float(predicted_load), 6)

    state["current_action_id"] = selected["id"]
    state["current_action_run_id"] = action_run_id
    state["current_action_status"] = ACTION_RESERVED
    state["execution_claim"] = None
    state["status"] = "action_reserved"
    state["next_step"] = f"执行 {selected['label']}（演示回放）"
    for evaluation in state["last_plan"]:
        if evaluation["id"] == selected["id"]:
            evaluation["decision"] = "SELECTED"
    graph = state["evidence_graph"]
    action_node = f"action:{state['id']}:{len(state['plan_history'])}:{selected['id']}"
    add_node(
        graph,
        action_node,
        f"推荐 {selected['label']}",
        "action",
        "selected",
        {
            "information_gain": selected["information_gain"],
            "risk_ratio": selected["risk_ratio"],
            "utility": selected["utility"],
            "predicted_load": selected["predicted_load"],
            "action_run_id": action_run_id,
        },
    )
    add_edge(
        graph, action_node, f"region:{state['id']}:{selected['region_id']}", "targets"
    )
    selected_with_run = {**selected, "action_run_id": action_run_id}
    return state, {
        "decision": "SELECTED_AND_RESERVED",
        "selected_action": selected_with_run,
        "evaluations": evaluations,
        "atomic_reservation": selected["predicted_load"],
        "action_run_id": action_run_id,
    }


def claim_action_for_execution(
    state: Dict[str, Any], action_run_id: str
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Atomically move one reserved action to EXECUTING.

    This function is designed to run only through ``SessionStore.atomic_update``.
    Exceptions abort that transaction, so concurrent losers cannot create an
    audit event, increment the session version, or invoke a physical adapter.
    """

    if not action_run_id:
        raise ValueError("action_run_id is required to claim an action")
    if any(
        item.get("action_run_id") == action_run_id
        for item in state.get("executions", [])
    ):
        raise ActionAlreadySettled(action_run_id)
    if state.get("current_action_run_id") != action_run_id:
        raise ValueError("action_run_id does not match the reserved action")

    action_id = state.get("current_action_id")
    if not action_id:
        raise ValueError("no action is reserved")
    action = next(
        (
            candidate
            for candidate in state["candidate_actions"]
            if candidate["id"] == action_id
        ),
        None,
    )
    if action is None:
        raise ValueError(f"unknown current action: {action_id}")

    # States written before this field existed are safely interpreted as
    # RESERVED when they still contain a current action and reservation.
    action_status = state.get("current_action_status") or ACTION_RESERVED
    if action_status == ACTION_EXECUTING:
        raise ActionExecutionInProgress(action_run_id)
    if action_status != ACTION_RESERVED:
        raise ValueError(f"action cannot be claimed from status {action_status}")

    claim = {
        "action_run_id": action_run_id,
        "action_id": action["id"],
        "region_id": action["region_id"],
        "modality": action["modality"],
        "predicted_load": deepcopy(action["predicted_load"]),
    }
    state["current_action_status"] = ACTION_EXECUTING
    state["execution_claim"] = claim
    state["status"] = "action_executing"
    state["next_step"] = f"正在执行 {action['label']}（演示回放）"
    return state, {"decision": "EXECUTION_CLAIMED", **deepcopy(claim)}


def conservative_adapter_failure_result(
    action: Dict[str, Any],
    action_run_id: str,
    *,
    error_category: str,
    adapter_error_type: str,
    adapter_response_hash: str | None = None,
) -> Dict[str, Any]:
    """Build a fail-closed result that consumes the reservation and locks risk."""

    result: Dict[str, Any] = {
        "profile": "adapter_failure_conservative",
        "protocol": "P01-ACTIVE-SENSING-DEMO-V1",
        "modality": action["modality"],
        "action_id": action["id"],
        "action_run_id": action_run_id,
        "region_id": action["region_id"],
        "actual_load": {},
        "telemetry_valid": {
            channel: False for channel in action.get("predicted_load", {})
        },
        "quality_metrics": {
            "device_ready": False,
            "calibration_valid": False,
            "snr_db": 0.0,
            "min_snr_db": 18.0,
            "saturation_ratio": 1.0,
            "region_match_valid": False,
            "spatial_registration_valid": False,
            "coverage_ratio": 0.0,
            "min_coverage_ratio": 0.90,
            "integrity_valid": False,
            "reference_applicability": False,
        },
        "finding": "仪器适配器未返回可验证结果；已按预留风险保守结算并锁定风险通道。",
        "evidence_status": "uncertain",
        "execution_status": "FAILED_CONSERVATIVE_SETTLEMENT",
        "error_category": error_category,
        "adapter_error_type": adapter_error_type,
        "demo_data": True,
        "source_category": "DEMO/SYNTHETIC",
        "device": {
            "id": "UNKNOWN",
            "adapter": "unavailable",
            "calibration": "UNVERIFIED",
        },
    }
    if adapter_response_hash:
        result["adapter_response_hash"] = adapter_response_hash
    return result


def _validate_settlement_identity(
    state: Dict[str, Any], action: Dict[str, Any], instrument_result: Dict[str, Any]
) -> None:
    if state.get("current_action_status") != ACTION_EXECUTING:
        raise ValueError("action must be EXECUTING before settlement")

    expected = {
        "action_run_id": state.get("current_action_run_id"),
        "action_id": action["id"],
        "region_id": action["region_id"],
        "modality": action["modality"],
    }
    claim = state.get("execution_claim")
    if not isinstance(claim, dict):
        raise ValueError("execution claim is missing")
    for field, expected_value in expected.items():
        if claim.get(field) != expected_value:
            raise ValueError(
                f"execution claim {field} does not match the reserved action"
            )
        if instrument_result.get(field) != expected_value:
            raise ValueError(
                f"instrument result {field} does not match the execution claim"
            )


def settle_and_gate(
    state: Dict[str, Any], instrument_result: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    action_id = state.get("current_action_id")
    action_run_id = state.get("current_action_run_id")
    if not action_id:
        raise ValueError("no action is reserved")
    action = next(
        (
            candidate
            for candidate in state["candidate_actions"]
            if candidate["id"] == action_id
        ),
        None,
    )
    if action is None:
        raise ValueError(f"unknown current action: {action_id}")
    if not isinstance(instrument_result, dict):
        raise ValueError("instrument result must be an object")

    # Validate all routing identifiers before touching any risk counter. This
    # prevents a delayed result from action N from settling action N+1.
    _validate_settlement_identity(state, action, instrument_result)
    if not isinstance(instrument_result.get("telemetry_valid"), dict):
        raise ValueError("instrument result telemetry_valid must be an object")
    if not isinstance(instrument_result.get("actual_load"), dict):
        raise ValueError("instrument result actual_load must be an object")
    if not isinstance(instrument_result.get("quality_metrics"), dict):
        raise ValueError("instrument result quality_metrics must be an object")

    settlement: Dict[str, Any] = {}
    for channel, predicted_load in action["predicted_load"].items():
        budget = _budget_for(state, action["region_id"], channel)
        telemetry_valid = bool(instrument_result["telemetry_valid"].get(channel, False))
        actual_increment = (
            max(
                0.0,
                float(instrument_result["actual_load"].get(channel, predicted_load)),
            )
            if telemetry_valid
            else float(predicted_load)
        )
        budget["used"] = round(float(budget["used"]) + actual_increment, 6)
        budget["reserved"] = round(
            max(0.0, float(budget["reserved"]) - float(predicted_load)), 6
        )
        if not telemetry_valid:
            budget["locked"] = True
        if float(budget["used"]) > float(budget["limit"]) + EPSILON:
            budget["locked"] = True
        settlement[channel] = {
            "telemetry_valid": telemetry_valid,
            "predicted": predicted_load,
            "settled": actual_increment,
            "used_after": budget["used"],
            "reserved_after": budget["reserved"],
            "locked": budget["locked"],
            "over_limit": float(budget["used"]) > float(budget["limit"]) + EPSILON,
        }

    # The physical exposure settlement above is deliberately completed before
    # the data-quality gate and proposition update below.
    gate = quality_gate(instrument_result)
    uncertainty_before = float(state["uncertainty"])
    uncertainty_after = uncertainty_before
    if gate["passed"]:
        uncertainty_after = float(action["expected_uncertainty"])
        state["uncertainty"] = round(uncertainty_after, 4)
        state["claim_consistency"] = "REVIEW_REQUIRED"
        state["status"] = (
            "complete" if uncertainty_after <= STOP_UNCERTAINTY else "evidence_updated"
        )
        state["next_step"] = (
            "生成可审计报告" if state["status"] == "complete" else "重新规划下一检测"
        )
    else:
        if action["retry_key"] not in state["retry_blocked"]:
            state["retry_blocked"].append(action["retry_key"])
        state["status"] = "quality_failed"
        state["next_step"] = "重新规划；原样重试已抑制"

    execution_index = len(state.get("executions", [])) + 1
    execution_record = {
        "index": execution_index,
        "action_run_id": action_run_id,
        "action": deepcopy(action),
        "result": instrument_result,
        "settlement": settlement,
        "quality_gate": gate,
        "uncertainty_before": round(uncertainty_before, 4),
        "uncertainty_after": round(uncertainty_after, 4),
    }
    state.setdefault("executions", []).append(execution_record)
    state["current_action_id"] = None
    state["current_action_run_id"] = None
    state["current_action_status"] = None
    state["execution_claim"] = None

    graph = state["evidence_graph"]
    observation_node = f"observation:{state['id']}:{execution_index}:{action_id}"
    evidence_node = f"evidence:{state['id']}:{execution_index}:{action_id}"
    node_status = instrument_result["evidence_status"] if gate["passed"] else "rejected"
    add_node(
        graph,
        observation_node,
        f"{action['modality']} 结果",
        "observation",
        node_status,
        {
            "quality_gate": gate,
            "settlement": settlement,
            "demo_data": bool(instrument_result.get("demo_data")),
            "source_category": instrument_result.get("source_category"),
            "action_run_id": action_run_id,
        },
    )
    add_edge(
        graph,
        observation_node,
        f"region:{state['id']}:{action['region_id']}",
        "measured_at",
    )
    add_node(
        graph,
        evidence_node,
        instrument_result["finding"],
        "evidence",
        node_status,
        {
            "gate_passed": gate["passed"],
            "modality": action["modality"],
            "demo_data": bool(instrument_result.get("demo_data")),
            "source_category": instrument_result.get("source_category"),
        },
    )
    relation = {
        "support": "supports",
        "conflict": "conflicts_with",
        "uncertain": "uncertain",
        "escalate": "escalates",
    }.get(instrument_result["evidence_status"], "uncertain")
    add_edge(
        graph,
        evidence_node,
        f"claim:{state['id']}",
        relation if gate["passed"] else "not_admitted",
        node_status,
        0.8 if gate["passed"] else 0.0,
    )
    add_edge(graph, evidence_node, observation_node, "derived_from", node_status)

    return state, {
        "action_id": action_id,
        "action_run_id": action_run_id,
        "operation_order": [
            "DEVICE_EXECUTION",
            "PHYSICAL_RISK_SETTLEMENT",
            "QUALITY_GATE",
            "PROPOSITION_UPDATE_OR_HOLD",
        ],
        "settlement": settlement,
        "quality_gate": gate,
        "uncertainty_before": uncertainty_before,
        "uncertainty_after": uncertainty_after,
        "retry_suppressed": not gate["passed"],
        "source_category": instrument_result.get("source_category"),
        "protocol": instrument_result.get("protocol"),
        "result_hash": instrument_result.get("result_hash"),
    }
