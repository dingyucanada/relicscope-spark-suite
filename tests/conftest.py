from __future__ import annotations

import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault(
    "RELICSCOPE_DATA_DIR", str(PROJECT_ROOT / "work" / "test-import-runtime")
)
os.environ.setdefault("RELICSCOPE_RUNTIME_MODE", "local-development")


class LocalModelStub:
    def __init__(self, role: str) -> None:
        self.role = role
        self.model = f"test-{role}-model"

    async def health(self, name: str):
        return {
            "name": name,
            "status": "online",
            "detail": "test model stub ready",
            "model": self.model,
            "latency_ms": 1,
        }

    async def vision_observe(self, image_data_url, metadata):
        return {
            "available": True,
            "mode": "local_test_stub",
            "role": "vision",
            "model": self.model,
            "prompt_hash": "1" * 64,
            "latency_ms": 1,
            "output_hash": "2" * 64,
            "output": {
                "observations": ["可见蓝色纹饰与高反光釉面"],
                "suggested_regions": [{"label": "R1", "reason": "纹饰边界清楚"}],
                "limitations": ["测试替身；不代表科学结论"],
                "ood_risk": "LOW",
            },
        }

    async def summarize_report(self, report):
        return {
            "available": True,
            "mode": "local_test_stub",
            "role": "reasoner",
            "model": self.model,
            "prompt_hash": "3" * 64,
            "latency_ms": 1,
            "output_hash": "4" * 64,
            "output": {
                "summary": "材料响应与送检声明存在待复核差异。",
                "limitations": ["测试替身与演示数据"],
                "next_steps": ["专家复核"],
                "citation_ids": [],
            },
        }


@pytest.fixture
def active_state() -> Dict[str, Any]:
    from app.services.active_sensing import DEFAULT_ACTIONS, default_budgets
    from app.services.evidence import build_initial_graph

    session_id = "RS-TEST-ACTIVE"
    return {
        "id": session_id,
        "uncertainty": 0.85,
        "status": "ready",
        "next_step": "规划下一检测",
        "claim_consistency": "EVIDENCE_INSUFFICIENT",
        "risk_budgets": default_budgets(),
        "candidate_actions": deepcopy(DEFAULT_ACTIONS),
        "retry_blocked": [],
        "current_action_id": None,
        "current_action_run_id": None,
        "plan_history": [],
        "executions": [],
        "evidence_graph": build_initial_graph(session_id, "测试器物", "清代景德镇青花瓷"),
    }


@pytest.fixture
def app_settings(tmp_path):
    from dataclasses import replace

    from app.config import Settings

    base = Settings.from_env()
    return replace(
        base,
        data_dir=tmp_path / "runtime",
        db_path=tmp_path / "runtime" / "test.sqlite3",
        upload_dir=tmp_path / "runtime" / "uploads",
        knowledge_manifest_path=PROJECT_ROOT / "data" / "knowledge_manifest.json",
        runtime_mode="local-development",
        offline_mode=True,
        vision_base_url="",
        reasoner_base_url="",
        embedding_base_url="",
    )


@pytest.fixture
def api_client(app_settings):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services.knowledge import KnowledgeBase

    knowledge = KnowledgeBase.from_path(
        app_settings.knowledge_manifest_path, offline=True
    )
    application = create_app(app_settings, knowledge=knowledge)
    application.state.service.vision_client = LocalModelStub("vision")
    application.state.service.reasoner_client = LocalModelStub("reasoner")
    with TestClient(application) as client:
        yield client
