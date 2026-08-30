#!/usr/bin/env python3
"""Verify bundled synthetic media provenance, checksums and decodability."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--media-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "demo_media",
    )
    args = parser.parse_args()
    media_dir = args.media_dir.resolve()
    manifest = json.loads((media_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("provenance") != "DEMO/SYNTHETIC":
        raise SystemExit("manifest provenance is not DEMO/SYNTHETIC")
    if manifest.get("contains_real_artifact_media") is not False:
        raise SystemExit("manifest does not explicitly exclude real artifact media")

    sum_rows = {}
    for line in (media_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        sum_rows[relative] = digest
    expected = {item["path"]: item["sha256"] for item in manifest["files"]}
    if expected != sum_rows:
        raise SystemExit("manifest.json and SHA256SUMS disagree")
    if len(expected) < 9:
        raise SystemExit("fixture must include two images, six frames and one video")

    image_count = 0
    for relative, digest in expected.items():
        path = media_dir / relative
        if not path.is_file() or _sha256(path) != digest:
            raise SystemExit(f"checksum mismatch: {relative}")
        if path.suffix == ".png":
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                if image.size != (768, 768):
                    raise SystemExit(f"unexpected image dimensions: {relative}")
                if not str(image.info.get("relicscope.provenance", "")).startswith(
                    "DEMO/SYNTHETIC"
                ):
                    raise SystemExit(f"missing synthetic provenance: {relative}")
            image_count += 1

    video = media_dir / "synthetic_orbit.mp4"
    prefix = video.read_bytes()[:12]
    if len(prefix) < 12 or prefix[4:8] != b"ftyp":
        raise SystemExit("synthetic_orbit.mp4 is not an ISO-BMFF file")
    ffprobe = shutil.which("ffprobe")
    video_details = "ISO-BMFF signature verified"
    if ffprobe:
        result = subprocess.run(
            [
                ffprobe, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,pix_fmt,width,height,duration",
                "-of", "json", str(video),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        stream = json.loads(result.stdout)["streams"][0]
        if stream.get("codec_name") != "h264" or stream.get("pix_fmt") != "yuv420p":
            raise SystemExit(f"video is not portable H.264/yuv420p: {stream}")
        if (stream.get("width"), stream.get("height")) != (768, 768):
            raise SystemExit(f"unexpected video dimensions: {stream}")
        video_details = f"H.264/{stream['pix_fmt']}, {stream.get('duration', '?')} s"

    print(
        json.dumps(
            {
                "status": "verified",
                "provenance": "DEMO/SYNTHETIC",
                "image_count": image_count,
                "video": video_details,
                "manifest_entries": len(expected),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
