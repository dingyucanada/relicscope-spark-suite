from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HTML = (PROJECT_ROOT / "app/static/index.html").read_text(encoding="utf-8")
JAVASCRIPT = (PROJECT_ROOT / "app/static/app.js").read_text(encoding="utf-8")


def test_console_is_explicitly_labeled_as_local_engineering_preview() -> None:
    assert "<title>RelicScope AI · Local Engineering Preview</title>" in HTML
    assert "LOCAL ENGINEERING PREVIEW" in HTML
    assert "Local Engineering Preview" in HTML
    assert "Scientific Forensics Console" not in HTML
    assert "Local Scientific Forensics Prototype" not in HTML


def test_api_offline_guidance_uses_the_safe_loopback_start_contract() -> None:
    assert 'id="api-unavailable-guidance"' in HTML
    assert 'role="alert"' in HTML
    assert "make console-install" in HTML
    assert "make console" in HTML
    assert "http://127.0.0.1:8088" in HTML
    assert "请勿改为公网监听" in HTML
    assert 'id="retry-api-connection"' in HTML
    assert '$("retry-api-connection").addEventListener("click", loadHealth);' in JAVASCRIPT
    assert "setApiAvailability(false, error);" in JAVASCRIPT
    assert "state.apiAvailable !== true" in JAVASCRIPT


def test_runtime_receipt_accepts_nim_and_vllm_without_conflating_them() -> None:
    assert '["local_nim", "local_vllm"]' in JAVASCRIPT
    assert '=== "local_nim" ? "NVIDIA NIM" : "LOCAL VLLM"' in JAVASCRIPT


def test_local_preview_does_not_present_fallback_pipelines_as_two_sparks() -> None:
    assert "LOCAL ENGINEERING · TWO PIPELINES" in JAVASCRIPT
    assert "AI 流程（本机回退）" in JAVASCRIPT
    assert "证据流程（本机回退）" in JAVASCRIPT
    assert "逻辑 A（本机回退）" not in JAVASCRIPT
    assert "逻辑 B（本机回退）" not in JAVASCRIPT


def test_scientific_and_demo_disclaimers_remain_visible() -> None:
    assert "USER MEDIA / DEMO SCIENCE" in HTML
    assert "输出不构成鉴定、断代、估价或法律结论" in HTML
    assert "当前无真实仪器测量" in HTML
    assert "模型观察不能独立形成鉴定结论" in HTML
    assert "相似性检索不能等同于身份、年代或真伪判断" in HTML


def test_local_application_serves_the_preview_and_browser_contract(api_client) -> None:
    page = api_client.get("/")
    script = api_client.get("/static/app.js")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "RelicScope AI · Local Engineering Preview" in page.text
    assert script.status_code == 200
    assert "fetchLocalApi" in script.text
