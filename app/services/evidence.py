from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional


NODE_COLORS = {
    "artifact": "#2b5b8e",
    "claim": "#a47b32",
    "region": "#5d806c",
    "raw": "#667487",
    "observation": "#3977aa",
    "action": "#9b5c49",
    "evidence": "#8c6d34",
    "report": "#26384d",
    "reference": "#6f7f8e",
    "model_run": "#735d8c",
}

ALLOWED_RELATIONS = {
    "claims_identity_of",
    "part_of",
    "targets",
    "measured_at",
    "derived_from",
    "supports",
    "conflicts_with",
    "uncertain",
    "escalates",
    "not_admitted",
    "cites",
    "produced_by",
    "analyzes",
    "summarizes",
}


def empty_graph() -> Dict[str, Any]:
    return {"nodes": [], "edges": []}


def add_node(
    graph: Dict[str, Any],
    node_id: str,
    label: str,
    node_type: str,
    status: str = "neutral",
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    existing = next((node for node in graph["nodes"] if node["id"] == node_id), None)
    payload = {
        "id": node_id,
        "label": label,
        "type": node_type,
        "status": status,
        "color": NODE_COLORS.get(node_type, "#667487"),
        "meta": meta or {},
    }
    if existing is None:
        graph["nodes"].append(payload)
    else:
        existing.update(payload)


def add_edge(
    graph: Dict[str, Any],
    source: str,
    target: str,
    relation: str,
    status: str = "neutral",
    weight: float = 1.0,
) -> None:
    if relation not in ALLOWED_RELATIONS:
        raise ValueError(f"unsupported evidence relation: {relation}")
    key = (source, target, relation)
    existing = next(
        (
            edge
            for edge in graph["edges"]
            if (edge["source"], edge["target"], edge["relation"]) == key
        ),
        None,
    )
    payload = {
        "source": source,
        "target": target,
        "relation": relation,
        "status": status,
        "weight": round(float(weight), 4),
    }
    if existing is None:
        graph["edges"].append(payload)
    else:
        existing.update(payload)


def build_initial_graph(session_id: str, artifact_name: str, claim_label: str) -> Dict[str, Any]:
    graph = empty_graph()
    artifact_id = f"artifact:{session_id}"
    claim_id = f"claim:{session_id}"
    region_id = f"region:{session_id}:R1"
    add_node(graph, artifact_id, artifact_name, "artifact", "active")
    add_node(graph, claim_id, claim_label, "claim", "uncertain")
    add_node(graph, region_id, "R1 蓝色纹饰区", "region", "active")
    add_edge(graph, claim_id, artifact_id, "claims_identity_of")
    add_edge(graph, region_id, artifact_id, "part_of")
    return graph


def canonical_evidence_graph(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Return a stable, order-independent representation of the graph."""

    nodes = sorted(
        graph.get("nodes", []),
        key=lambda item: (str(item.get("id", "")), str(item.get("type", ""))),
    )
    edges = sorted(
        graph.get("edges", []),
        key=lambda item: (
            str(item.get("source", "")),
            str(item.get("target", "")),
            str(item.get("relation", "")),
        ),
    )
    return {"nodes": nodes, "edges": edges}


def evidence_graph_sha256(graph: Dict[str, Any]) -> str:
    payload = json.dumps(
        canonical_evidence_graph(graph),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
