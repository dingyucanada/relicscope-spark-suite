class UnboundReasonerStub:
    model = "unbound-reasoner-stub"

    async def summarize_report(self, report):
        return {
            "available": True,
            "mode": "local_test_stub",
            "model": self.model,
            "prompt_hash": "1" * 64,
            "output_hash": "2" * 64,
            "latency_ms": 1,
            "output": {
                "summary": "引用了未进入当前证据包的本地资料。",
                "limitations": ["测试替身"],
                "next_steps": ["专家复核"],
                "citation_ids": ["KB-NOT-IN-REPORT"],
            },
        }


def test_unbound_reasoner_citation_falls_back_without_blocking_report(api_client):
    created = api_client.post(
        "/api/sessions",
        json={
            "artifact_name": "引用边界测试器物",
            "operator": "Tester",
            "institution": "RelicScope Test Lab",
        },
    )
    session_id = created.json()["session"]["id"]
    api_client.app.state.service.reasoner_client = UnboundReasonerStub()

    response = api_client.post(f"/api/sessions/{session_id}/report")

    assert response.status_code == 200
    report = response.json()["session"]["last_report"]
    assert report["assistant_summary"]["citation_ids"] == []
    assert "未参与摘要" in report["assistant_summary"]["summary"]
    run = report["model_runs"][-1]
    assert run["status"] == "DEGRADED"
    assert run["error_category"] == "ReasonerCitationOrBoundaryViolation"
