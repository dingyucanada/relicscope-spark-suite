from __future__ import annotations

from pathlib import Path

import pytest

from app.services.video_analysis import inspect_mp4_bytes


DEMO_MP4 = Path(__file__).parents[1] / "demo_media" / "synthetic_orbit.mp4"


def test_real_demo_mp4_metadata_is_parsed_from_container_bytes():
    metadata = inspect_mp4_bytes(
        DEMO_MP4.read_bytes(),
        max_duration_ms=10_000,
    )

    assert metadata["duration_ms"] == pytest.approx(3_000, abs=1)
    assert metadata["width"] == 768
    assert metadata["height"] == 768
    assert metadata["codec"] == "H264"
    assert metadata["frame_metadata"]["codec_fourcc"] in {"avc1", "avc3"}
    assert metadata["frame_metadata"]["sample_count"] > 0
    assert metadata["frame_metadata"]["frame_rate"] > 0
    assert metadata["frame_metadata"]["coded_width"] == 768
    assert metadata["frame_metadata"]["coded_height"] == 768
    assert metadata["container_metadata"]["container"] == "ISO-BMFF/MP4"
    assert metadata["container_metadata"]["major_brand"] == "isom"
    assert metadata["container_metadata"]["movie_timescale"] > 0


def test_spoofed_ftyp_magic_without_real_mp4_structure_is_rejected():
    fake_mp4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"v" * 512

    with pytest.raises(ValueError):
        inspect_mp4_bytes(fake_mp4, max_duration_ms=10_000)


def test_real_mp4_over_callers_duration_limit_is_rejected():
    with pytest.raises(ValueError, match="duration exceeds"):
        inspect_mp4_bytes(DEMO_MP4.read_bytes(), max_duration_ms=2_999)


def test_non_h264_sample_entry_is_rejected_even_with_valid_mp4_boxes():
    data = DEMO_MP4.read_bytes()
    stsd_index = data.index(b"stsd")
    avc1_index = data.index(b"avc1", stsd_index)
    forged = data[:avc1_index] + b"hvc1" + data[avc1_index + 4 :]

    with pytest.raises(ValueError, match="unsupported video codec"):
        inspect_mp4_bytes(forged, max_duration_ms=10_000)


def test_non_positive_movie_duration_is_rejected():
    forged = bytearray(DEMO_MP4.read_bytes())
    mvhd_type_offset = forged.index(b"mvhd")
    assert forged[mvhd_type_offset + 4] == 0  # version 0 mvhd in the demo asset
    forged[mvhd_type_offset + 20 : mvhd_type_offset + 24] = b"\x00" * 4

    with pytest.raises(ValueError, match="positive duration"):
        inspect_mp4_bytes(bytes(forged), max_duration_ms=10_000)


def test_movie_duration_must_agree_with_video_track_timeline():
    forged = bytearray(DEMO_MP4.read_bytes())
    mvhd_type_offset = forged.index(b"mvhd")
    assert forged[mvhd_type_offset + 4] == 0
    forged[mvhd_type_offset + 20 : mvhd_type_offset + 24] = (1_000).to_bytes(
        4, "big"
    )

    with pytest.raises(ValueError, match="durations disagree"):
        inspect_mp4_bytes(bytes(forged), max_duration_ms=10_000)


def test_square_4k_video_exceeding_bounded_pixel_area_is_rejected():
    forged = bytearray(DEMO_MP4.read_bytes())
    tkhd_type_offset = forged.index(b"tkhd")
    assert forged[tkhd_type_offset + 4] == 0  # version 0 tkhd in the demo asset
    fixed_4096 = (4_096 << 16).to_bytes(4, "big")
    forged[tkhd_type_offset + 80 : tkhd_type_offset + 84] = fixed_4096
    forged[tkhd_type_offset + 84 : tkhd_type_offset + 88] = fixed_4096

    stsd_type_offset = forged.index(b"stsd")
    avc1_type_offset = forged.index(b"avc1", stsd_type_offset)
    forged[avc1_type_offset + 28 : avc1_type_offset + 30] = (4_096).to_bytes(
        2, "big"
    )
    forged[avc1_type_offset + 30 : avc1_type_offset + 32] = (4_096).to_bytes(
        2, "big"
    )

    with pytest.raises(ValueError, match="4K pixel limit"):
        inspect_mp4_bytes(bytes(forged), max_duration_ms=10_000)
