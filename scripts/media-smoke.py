#!/usr/bin/env python3
"""Run a real-media acceptance pass against a running RelicScope service."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

import httpx


IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
VIDEO_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
}


def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> dict:
    response = client.request(method, path, **kwargs)
    if response.is_error:
        raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text}")
    return response.json()


def _mime(path: Path, supported: dict[str, str]) -> str:
    mime_type = supported.get(path.suffix.lower())
    if mime_type:
        return mime_type
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed in supported.values():
        return guessed
    raise ValueError(f"unsupported media type: {path}")


def _image_payload(path: Path, *, region_id: str) -> dict[str, Any]:
    return {
        "filename": path.name,
        "mime_type": _mime(path, IMAGE_MIME_TYPES),
        "image_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        "modality": "RGB",
        "region_id": region_id,
    }


def _frame_payloads(frame_dir: Path, duration_ms: int, maximum: int) -> list[dict]:
    paths = sorted(
        path
        for path in frame_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_MIME_TYPES
    )[:maximum]
    if len(paths) < 3:
        raise ValueError("frames-dir must contain at least three JPEG/PNG/WEBP frames")
    denominator = max(1, len(paths) - 1)
    return [
        {
            "timestamp_ms": round(index * duration_ms / denominator),
            "mime_type": _mime(path, IMAGE_MIME_TYPES),
            "image_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
        for index, path in enumerate(paths)
    ]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exercise image, recapture, video, report and integrity paths."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--comparison-image", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--frames-dir", type=Path, required=True)
    parser.add_argument("--duration-ms", type=int, default=6000)
    parser.add_argument("--max-frames", type=int, default=10, choices=range(3, 13))
    parser.add_argument("--region-id", default="R1")
    parser.add_argument("--report-json", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    for path in (args.image, args.comparison_image, args.video, args.frames_dir):
        if not path.exists():
            raise FileNotFoundError(path)
    if args.duration_ms <= 0:
        raise ValueError("duration-ms must be positive")

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=120.0) as client:
        health = _request(client, "GET", "/api/health")
        created = _request(
            client,
            "POST",
            "/api/sessions",
            json={
                "artifact_name": "媒体闭环自动验收瓷器",
                "operator": "RelicScope Media Smoke",
                "institution": "RelicScope Local Lab",
                "claim": {
                    "period": "待核验",
                    "kiln": "待核验",
                    "material": "陶瓷",
                    "provenance_note": "本地软件验收；不构成鉴定结论",
                },
            },
        )
        session_id = created["session"]["id"]

        analyses = []
        for image_path in (args.image, args.comparison_image):
            result = _request(
                client,
                "POST",
                f"/api/sessions/{session_id}/images/analyze",
                json=_image_payload(image_path, region_id=args.region_id),
            )
            analyses.append(result["session"]["image_analyses"][-1])

        compared = _request(
            client,
            "POST",
            f"/api/sessions/{session_id}/images/compare",
            json={
                "baseline_analysis_id": analyses[0]["id"],
                "comparison_analysis_id": analyses[1]["id"],
            },
        )["session"]["image_comparisons"][-1]

        video_mime = _mime(args.video, VIDEO_MIME_TYPES)
        with args.video.open("rb") as video_handle:
            registered = _request(
                client,
                "POST",
                f"/api/sessions/{session_id}/videos/register",
                files={"file": (args.video.name, video_handle, video_mime)},
                data={
                    "modality": "RGB_VIDEO",
                    "region_id": args.region_id,
                    "duration_ms": str(args.duration_ms),
                    "capture_note": "media-smoke.py local acceptance",
                },
            )
        video = registered["session"]["videos"][-1]
        frames = _frame_payloads(args.frames_dir, args.duration_ms, args.max_frames)
        analyzed = _request(
            client,
            "POST",
            f"/api/sessions/{session_id}/videos/{video['id']}/analyze",
            json={
                "duration_ms": args.duration_ms,
                "sampling_strategy": "manual-keyframes-v1",
                "frames": frames,
            },
        )["session"]["video_analyses"][-1]

        report_envelope = _request(
            client, "POST", f"/api/sessions/{session_id}/report"
        )
        report = report_envelope["session"]["last_report"]
        integrity = _request(
            client, "GET", f"/api/sessions/{session_id}/integrity"
        )["integrity"]

    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    summary = {
        "service_status": health.get("status"),
        "runtime_mode": health.get("mode"),
        "session_id": session_id,
        "image_quality_passed": [
            item.get("quality", {}).get("passed") for item in analyses
        ],
        "recapture_status": compared.get("status"),
        "video_id": video.get("id"),
        "video_sha256": video.get("sha256"),
        "video_quality_passed": analyzed.get("quality", {}).get("passed"),
        "video_sampling_summary": analyzed.get("sampling_summary", {}),
        "representative_frame_count": len(
            analyzed.get("representative_frame_ids", [])
        ),
        "next_best_observation_count": len(
            report.get("next_best_observations", [])
        ),
        "evidence_nodes": len(report.get("evidence_graph", {}).get("nodes", [])),
        "evidence_edges": len(report.get("evidence_graph", {}).get("edges", [])),
        "report_id": report.get("report_id"),
        "report_sha256": report.get("integrity", {}).get("report_sha256"),
        "integrity_valid": integrity.get("valid"),
        "boundary": report.get("disclaimer"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
