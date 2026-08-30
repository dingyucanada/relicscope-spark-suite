from app.services.reporting import report_to_html


def test_human_report_contains_all_required_audit_sections():
    report = {
        "report_id": "RPT-TEST",
        "covered_session_version": 7,
        "generated_at": "2026-08-28T00:00:00+00:00",
        "protocol": {"id": "P01", "version": "1"},
        "artifact": {"name": "演示器物"},
        "claim": {"material": "瓷"},
        "claim_consistency": "REVIEW_REQUIRED",
        "uncertainty": 0.48,
        "conclusion_boundary": "仅描述科学证据状态。",
        "source_category": "DEMO/SYNTHETIC",
        "disclaimer": "DEMO/SYNTHETIC；这是非真实鉴定结论。",
        "raw_files": [
            {
                "filename": "artifact.png",
                "modality": "RGB",
                "region_id": "R1",
                "sha256": "a" * 64,
            }
        ],
        "image_analyses": [
            {
                "id": "IMG-1",
                "modality": "RGB",
                "region_id": "R1",
                "quality": {"passed": True, "failed_checks": []},
                "fingerprint": {"id": "b" * 64},
            }
        ],
        "model_runs": [
            {
                "run_id": "MRUN-1",
                "node_id": "spark-a",
                "role": "multimodal_observation",
                "model": "local-vlm",
                "status": "SUCCESS",
                "mode": "private_endpoint",
                "input_hash": "c" * 64,
                "output_hash": "d" * 64,
            }
        ],
        "executions": [
            {
                "action": {"label": "HSI @ R1"},
                "quality_gate": {"passed": True},
                "uncertainty_before": 0.85,
                "uncertainty_after": 0.48,
                "result": {"source_category": "DEMO/SYNTHETIC"},
            }
        ],
        "risk_budgets": {
            "R1": {
                "photochemical": {
                    "used": 0.78,
                    "reserved": 0.0,
                    "limit": 1.0,
                    "unit": "J/cm²",
                }
            }
        },
        "knowledge": {"searches": [], "version": "KB-V1"},
        "evidence_graph": {
            "nodes": [{"id": "artifact:1"}],
            "edges": [{"relation": "conflicts_with"}],
        },
        "audit": {"valid": True, "event_count": 8, "latest_hash": "e" * 64},
        "assistant_summary": {
            "summary": "存在冲突证据，需专家复核。",
            "citation_ids": [],
        },
        "limitations": ["演示数据。"],
        "next_steps": ["专家复核。"],
        "integrity": {"report_sha256": "f" * 64, "latest_audit_hash": "e" * 64},
    }

    rendered = report_to_html(report)

    for heading in (
        "图像质量与视觉指纹",
        "模型运行追溯",
        "区域风险账本",
        "证据图摘要",
        "审计链验证",
        "保守摘要",
    ):
        assert heading in rendered
    assert "DEMO/SYNTHETIC" in rendered
    assert "非真实鉴定结论" in rendered
    assert "conflicts_with" in rendered
    assert "spark-a" in rendered
