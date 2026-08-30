from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Tuple


SUPPORTED_VIDEO_MIME_TYPES = {
    "video/mp4",
    "video/x-m4v",
    "video/quicktime",
    "video/webm",
    "video/x-msvideo",
}

VIDEO_EXTENSIONS = {
    "ISO-BMFF": ".mp4",
    "WEBM": ".webm",
    "AVI": ".avi",
}

MAX_MP4_BOXES = 16_384
MAX_MP4_VIDEO_DIMENSION = 4_096
MAX_MP4_VIDEO_PIXELS = 4_096 * 2_160
SUPPORTED_MP4_BRANDS = {
    b"isom",
    b"iso2",
    b"iso3",
    b"iso4",
    b"iso5",
    b"iso6",
    b"avc1",
    b"mp41",
    b"mp42",
    b"M4V ",
    b"M4VH",
    b"M4VP",
    b"dash",
    b"cmfc",
    b"cmfs",
}
SUPPORTED_H264_SAMPLE_ENTRIES = {b"avc1", b"avc3"}


@dataclass(frozen=True)
class _Mp4Box:
    box_type: bytes
    start: int
    payload_start: int
    end: int


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    return struct.unpack_from(">Q", data, offset)[0]


def _fourcc(value: bytes) -> str:
    return value.decode("ascii", errors="replace")


def _iter_mp4_boxes(
    data: bytes,
    start: int,
    end: int,
    *,
    context: str,
) -> Iterable[_Mp4Box]:
    """Yield bounded ISO-BMFF boxes and reject ambiguous/truncated layouts."""

    cursor = start
    box_count = 0
    while cursor < end:
        box_count += 1
        if box_count > MAX_MP4_BOXES:
            raise ValueError(f"too many boxes in {context}")
        if end - cursor < 8:
            raise ValueError(f"trailing or truncated bytes in {context}")

        size_32 = _u32(data, cursor)
        box_type = data[cursor + 4 : cursor + 8]
        header_size = 8
        if size_32 == 1:
            if end - cursor < 16:
                raise ValueError(f"truncated extended-size box in {context}")
            box_size = _u64(data, cursor + 8)
            header_size = 16
        elif size_32 == 0:
            box_size = end - cursor
        else:
            box_size = size_32

        if box_size < header_size:
            raise ValueError(f"invalid box size in {context}")
        box_end = cursor + box_size
        if box_end > end:
            raise ValueError(f"box exceeds {context} boundary")
        yield _Mp4Box(box_type, cursor, cursor + header_size, box_end)
        cursor = box_end
        if size_32 == 0:
            break

    if cursor != end:
        raise ValueError(f"invalid box alignment in {context}")


def _children(data: bytes, parent: _Mp4Box) -> List[_Mp4Box]:
    return list(
        _iter_mp4_boxes(
            data,
            parent.payload_start,
            parent.end,
            context=_fourcc(parent.box_type),
        )
    )


def _one_box(boxes: Iterable[_Mp4Box], box_type: bytes, *, context: str) -> _Mp4Box:
    matches = [box for box in boxes if box.box_type == box_type]
    if len(matches) != 1:
        raise ValueError(
            f"{context} must contain exactly one {_fourcc(box_type)} box"
        )
    return matches[0]


def _parse_ftyp(data: bytes, box: _Mp4Box) -> Dict[str, Any]:
    payload_length = box.end - box.payload_start
    if payload_length < 8 or (payload_length - 8) % 4:
        raise ValueError("invalid ftyp box")
    major_brand = data[box.payload_start : box.payload_start + 4]
    compatible_brands = [
        data[offset : offset + 4]
        for offset in range(box.payload_start + 8, box.end, 4)
    ]
    if not ({major_brand, *compatible_brands} & SUPPORTED_MP4_BRANDS):
        raise ValueError("ISO-BMFF file does not declare a supported MP4 brand")
    return {
        "major_brand": _fourcc(major_brand),
        "minor_version": _u32(data, box.payload_start + 4),
        "compatible_brands": [_fourcc(brand) for brand in compatible_brands],
    }


def _parse_versioned_duration(
    data: bytes,
    box: _Mp4Box,
    *,
    context: str,
) -> Tuple[int, int]:
    payload_length = box.end - box.payload_start
    if payload_length < 4:
        raise ValueError(f"truncated {context} box")
    version = data[box.payload_start]
    if version == 0:
        if payload_length < 20:
            raise ValueError(f"truncated version 0 {context} box")
        timescale = _u32(data, box.payload_start + 12)
        duration = _u32(data, box.payload_start + 16)
    elif version == 1:
        if payload_length < 32:
            raise ValueError(f"truncated version 1 {context} box")
        timescale = _u32(data, box.payload_start + 20)
        duration = _u64(data, box.payload_start + 24)
    else:
        raise ValueError(f"unsupported {context} version")
    if timescale <= 0 or duration <= 0:
        raise ValueError(f"{context} must declare a positive duration")
    return timescale, duration


def _duration_ms(timescale: int, duration: int) -> int:
    return (duration * 1_000 + timescale // 2) // timescale


def _parse_tkhd(data: bytes, box: _Mp4Box) -> Dict[str, int]:
    payload_length = box.end - box.payload_start
    if payload_length < 4:
        raise ValueError("truncated tkhd box")
    version = data[box.payload_start]
    if version == 0:
        required_length = 84
        track_id_offset = 12
        dimensions_offset = 76
    elif version == 1:
        required_length = 96
        track_id_offset = 20
        dimensions_offset = 88
    else:
        raise ValueError("unsupported tkhd version")
    if payload_length < required_length:
        raise ValueError("truncated tkhd box")

    track_id = _u32(data, box.payload_start + track_id_offset)
    width_fixed = _u32(data, box.payload_start + dimensions_offset)
    height_fixed = _u32(data, box.payload_start + dimensions_offset + 4)
    if track_id <= 0:
        raise ValueError("video track has an invalid track id")
    if (width_fixed & 0xFFFF) or (height_fixed & 0xFFFF):
        raise ValueError("fractional video display dimensions are not supported")
    width = width_fixed >> 16
    height = height_fixed >> 16
    if width <= 0 or height <= 0:
        raise ValueError("video track must declare positive dimensions")
    return {"track_id": track_id, "width": width, "height": height}


def _parse_hdlr(data: bytes, box: _Mp4Box) -> str:
    if box.end - box.payload_start < 24:
        raise ValueError("truncated hdlr box")
    return _fourcc(data[box.payload_start + 8 : box.payload_start + 12])


def _parse_avcc(data: bytes, box: _Mp4Box, *, require_parameter_sets: bool) -> Dict[str, int]:
    payload = data[box.payload_start : box.end]
    if len(payload) < 7 or payload[0] != 1:
        raise ValueError("invalid avcC decoder configuration")
    nal_length_size = (payload[4] & 0x03) + 1
    if nal_length_size not in {1, 2, 4}:
        raise ValueError("unsupported H.264 NAL length size")

    cursor = 6
    sequence_parameter_set_count = payload[5] & 0x1F
    for _ in range(sequence_parameter_set_count):
        if cursor + 2 > len(payload):
            raise ValueError("truncated H.264 sequence parameter set")
        parameter_length = _u16(payload, cursor)
        cursor += 2
        if parameter_length <= 0 or cursor + parameter_length > len(payload):
            raise ValueError("invalid H.264 sequence parameter set")
        cursor += parameter_length
    if cursor >= len(payload):
        raise ValueError("missing H.264 picture parameter set count")

    picture_parameter_set_count = payload[cursor]
    cursor += 1
    for _ in range(picture_parameter_set_count):
        if cursor + 2 > len(payload):
            raise ValueError("truncated H.264 picture parameter set")
        parameter_length = _u16(payload, cursor)
        cursor += 2
        if parameter_length <= 0 or cursor + parameter_length > len(payload):
            raise ValueError("invalid H.264 picture parameter set")
        cursor += parameter_length
    if require_parameter_sets and (
        sequence_parameter_set_count == 0 or picture_parameter_set_count == 0
    ):
        raise ValueError("avc1 track is missing H.264 parameter sets")
    return {
        "avc_profile_idc": payload[1],
        "avc_level_idc": payload[3],
        "nal_length_size": nal_length_size,
        "sequence_parameter_set_count": sequence_parameter_set_count,
        "picture_parameter_set_count": picture_parameter_set_count,
    }


def _parse_stsd(data: bytes, box: _Mp4Box) -> Dict[str, Any]:
    if box.end - box.payload_start < 8:
        raise ValueError("truncated stsd box")
    entry_count = _u32(data, box.payload_start + 4)
    if not 1 <= entry_count <= 32:
        raise ValueError("stsd must contain a bounded, non-empty sample entry list")
    entries = list(
        _iter_mp4_boxes(
            data,
            box.payload_start + 8,
            box.end,
            context="stsd",
        )
    )
    if len(entries) != entry_count:
        raise ValueError("stsd entry count does not match its sample entries")

    parsed_entries = []
    for entry in entries:
        if entry.box_type not in SUPPORTED_H264_SAMPLE_ENTRIES:
            raise ValueError(
                f"unsupported video codec sample entry: {_fourcc(entry.box_type)}"
            )
        if entry.end - entry.payload_start < 78:
            raise ValueError("truncated visual sample entry")
        coded_width = _u16(data, entry.payload_start + 24)
        coded_height = _u16(data, entry.payload_start + 26)
        if coded_width <= 0 or coded_height <= 0:
            raise ValueError("visual sample entry has invalid dimensions")
        child_boxes = list(
            _iter_mp4_boxes(
                data,
                entry.payload_start + 78,
                entry.end,
                context=f"{_fourcc(entry.box_type)} sample entry",
            )
        )
        avcc = _one_box(child_boxes, b"avcC", context=_fourcc(entry.box_type))
        parsed_entries.append(
            {
                "codec_fourcc": _fourcc(entry.box_type),
                "coded_width": coded_width,
                "coded_height": coded_height,
                **_parse_avcc(
                    data,
                    avcc,
                    require_parameter_sets=entry.box_type == b"avc1",
                ),
            }
        )

    codecs = {entry["codec_fourcc"] for entry in parsed_entries}
    dimensions = {
        (entry["coded_width"], entry["coded_height"]) for entry in parsed_entries
    }
    if len(codecs) != 1 or len(dimensions) != 1:
        raise ValueError("ambiguous H.264 sample descriptions are not supported")
    return {"sample_entry_count": entry_count, **parsed_entries[0]}


def _parse_stts(data: bytes, box: _Mp4Box) -> Dict[str, int]:
    if box.end - box.payload_start < 8:
        raise ValueError("truncated stts box")
    entry_count = _u32(data, box.payload_start + 4)
    if not 1 <= entry_count <= MAX_MP4_BOXES:
        raise ValueError("stts must contain a bounded, non-empty timing table")
    required_end = box.payload_start + 8 + entry_count * 8
    if required_end != box.end:
        raise ValueError("stts entry count does not match its timing table")
    sample_count = 0
    media_duration = 0
    for offset in range(box.payload_start + 8, box.end, 8):
        run_sample_count = _u32(data, offset)
        sample_delta = _u32(data, offset + 4)
        if run_sample_count <= 0 or sample_delta <= 0:
            raise ValueError("stts contains a non-positive timing run")
        sample_count += run_sample_count
        media_duration += run_sample_count * sample_delta
    return {"sample_count": sample_count, "media_duration": media_duration}


def _parse_stsz(data: bytes, box: _Mp4Box) -> int:
    if box.end - box.payload_start < 12:
        raise ValueError("truncated stsz box")
    constant_sample_size = _u32(data, box.payload_start + 4)
    sample_count = _u32(data, box.payload_start + 8)
    if sample_count <= 0:
        raise ValueError("stsz must declare at least one sample")
    expected_length = 12 if constant_sample_size else 12 + sample_count * 4
    if box.end - box.payload_start != expected_length:
        raise ValueError("stsz sample count does not match its size table")
    return sample_count


def _parse_video_track(data: bytes, trak: _Mp4Box) -> Optional[Dict[str, Any]]:
    trak_children = _children(data, trak)
    tkhd = _one_box(trak_children, b"tkhd", context="trak")
    mdia = _one_box(trak_children, b"mdia", context="trak")
    mdia_children = _children(data, mdia)
    hdlr = _one_box(mdia_children, b"hdlr", context="mdia")
    if _parse_hdlr(data, hdlr) != "vide":
        return None

    tkhd_metadata = _parse_tkhd(data, tkhd)
    mdhd = _one_box(mdia_children, b"mdhd", context="video mdia")
    media_timescale, media_duration = _parse_versioned_duration(
        data, mdhd, context="mdhd"
    )
    minf = _one_box(mdia_children, b"minf", context="video mdia")
    stbl = _one_box(_children(data, minf), b"stbl", context="video minf")
    stbl_children = _children(data, stbl)
    stsd_metadata = _parse_stsd(
        data, _one_box(stbl_children, b"stsd", context="video stbl")
    )
    stts_metadata = _parse_stts(
        data, _one_box(stbl_children, b"stts", context="video stbl")
    )
    declared_media_duration_ms = _duration_ms(media_timescale, media_duration)
    sample_timeline_duration_ms = _duration_ms(
        media_timescale, stts_metadata["media_duration"]
    )
    duration_tolerance_ms = max(500, round(declared_media_duration_ms * 0.05))
    if (
        abs(declared_media_duration_ms - sample_timeline_duration_ms)
        > duration_tolerance_ms
    ):
        raise ValueError("video media duration and sample timeline disagree")
    stsz_sample_count = _parse_stsz(
        data, _one_box(stbl_children, b"stsz", context="video stbl")
    )
    if stts_metadata["sample_count"] != stsz_sample_count:
        raise ValueError("video timing and sample-size tables disagree")
    if (
        tkhd_metadata["width"] != stsd_metadata["coded_width"]
        or tkhd_metadata["height"] != stsd_metadata["coded_height"]
    ):
        raise ValueError("video display and coded dimensions disagree")

    frames_per_second = (
        stts_metadata["sample_count"]
        * media_timescale
        / stts_metadata["media_duration"]
    )
    if not math.isfinite(frames_per_second) or frames_per_second <= 0:
        raise ValueError("video frame timing is invalid")
    return {
        **tkhd_metadata,
        **stsd_metadata,
        "media_timescale": media_timescale,
        "media_duration_ms": declared_media_duration_ms,
        "sample_timeline_duration_ms": sample_timeline_duration_ms,
        "sample_count": stts_metadata["sample_count"],
        "frame_rate": round(frames_per_second, 3),
    }


def inspect_mp4_bytes(data: bytes, *, max_duration_ms: int) -> Dict[str, Any]:
    """Validate an MP4 from its bytes and return verified H.264 metadata.

    The parser intentionally supports a conservative subset of ISO-BMFF:
    one conventional, non-fragmented H.264 video track with explicit sample
    tables. Files that are truncated, ambiguous, oversized, or merely spoof an
    ``ftyp`` signature are rejected before multimodal inference.
    """

    if not isinstance(data, bytes):
        raise ValueError("MP4 input must be bytes")
    if max_duration_ms <= 0:
        raise ValueError("maximum video duration must be positive")
    if len(data) < 16:
        raise ValueError("truncated MP4 file")

    top_level = list(_iter_mp4_boxes(data, 0, len(data), context="MP4 file"))
    if not top_level or top_level[0].box_type != b"ftyp":
        raise ValueError("MP4 must begin with an ftyp box")
    ftyp = _one_box(top_level, b"ftyp", context="MP4 file")
    moov = _one_box(top_level, b"moov", context="MP4 file")
    ftyp_metadata = _parse_ftyp(data, ftyp)

    moov_children = _children(data, moov)
    mvhd = _one_box(moov_children, b"mvhd", context="moov")
    movie_timescale, movie_duration = _parse_versioned_duration(
        data, mvhd, context="mvhd"
    )
    duration_ms = _duration_ms(movie_timescale, movie_duration)
    if duration_ms <= 0:
        raise ValueError("MP4 duration must be positive")
    if movie_duration * 1_000 > max_duration_ms * movie_timescale:
        raise ValueError("MP4 duration exceeds configured maximum")

    video_tracks = []
    for trak in (box for box in moov_children if box.box_type == b"trak"):
        parsed = _parse_video_track(data, trak)
        if parsed is not None:
            video_tracks.append(parsed)
    if len(video_tracks) != 1:
        raise ValueError("MP4 must contain exactly one unambiguous video track")
    video_track = video_tracks[0]
    track_duration_ms = int(video_track["media_duration_ms"])
    duration_tolerance_ms = max(500, round(duration_ms * 0.05))
    if abs(duration_ms - track_duration_ms) > duration_tolerance_ms:
        raise ValueError("MP4 movie and video-track durations disagree")
    width = int(video_track["width"])
    height = int(video_track["height"])
    if (
        width > MAX_MP4_VIDEO_DIMENSION
        or height > MAX_MP4_VIDEO_DIMENSION
        or width * height > MAX_MP4_VIDEO_PIXELS
    ):
        raise ValueError("MP4 dimensions exceed the supported 4K pixel limit")

    frame_metadata = {
        key: video_track[key]
        for key in (
            "track_id",
            "codec_fourcc",
            "coded_width",
            "coded_height",
            "sample_entry_count",
            "sample_count",
            "frame_rate",
            "media_timescale",
            "media_duration_ms",
            "sample_timeline_duration_ms",
            "avc_profile_idc",
            "avc_level_idc",
            "nal_length_size",
            "sequence_parameter_set_count",
            "picture_parameter_set_count",
        )
    }
    return {
        "duration_ms": duration_ms,
        "width": width,
        "height": height,
        "codec": "H264",
        "frame_metadata": frame_metadata,
        "container_metadata": {
            "container": "ISO-BMFF/MP4",
            **ftyp_metadata,
            "movie_timescale": movie_timescale,
            "movie_duration_units": movie_duration,
            "video_track_count": len(video_tracks),
        },
    }


def detect_video_container(prefix: bytes) -> str:
    """Identify the small set of containers accepted by the demo.

    This intentionally validates container signatures only. It does not claim
    that every codec track is decodable; frame extraction remains a separate,
    browser-side step with its own evidence record.
    """

    if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
        return "ISO-BMFF"
    if prefix.startswith(b"\x1aE\xdf\xa3"):
        return "WEBM"
    if len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"AVI ":
        return "AVI"
    raise ValueError("unsupported or damaged video container")


def validate_video_mime(declared_mime: str, detected_container: str) -> str:
    normalized = (declared_mime or "").split(";", 1)[0].strip().lower()
    if normalized not in SUPPORTED_VIDEO_MIME_TYPES:
        raise ValueError(f"unsupported video MIME: {normalized or 'missing'}")
    allowed = {
        "ISO-BMFF": {"video/mp4", "video/x-m4v", "video/quicktime"},
        "WEBM": {"video/webm"},
        "AVI": {"video/x-msvideo"},
    }[detected_container]
    if normalized not in allowed:
        raise ValueError(
            f"declared MIME {normalized} does not match {detected_container} container"
        )
    return normalized


def dhash_distance(first: str, second: str) -> int:
    try:
        return (int(first, 16) ^ int(second, 16)).bit_count()
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid perceptual hash") from exc


def _quality_score(frame: Dict[str, Any]) -> float:
    quality = frame["analysis"]["quality_gate"]
    metrics = frame["analysis"]["metrics"]
    checks = quality.get("checks", {})
    check_score = sum(bool(value) for value in checks.values()) / max(len(checks), 1)
    sharpness = min(1.0, float(metrics.get("sharpness_score", 0.0)) / 120.0)
    dynamic_range = min(
        1.0, float(metrics.get("dynamic_range_p01_p99", 0.0)) / 180.0
    )
    clipping = max(
        0.0,
        1.0
        - float(metrics.get("clipped_black_ratio", 0.0))
        - float(metrics.get("clipped_white_ratio", 0.0)),
    )
    return round(0.65 * check_score + 0.15 * sharpness + 0.10 * dynamic_range + 0.10 * clipping, 4)


def _mean_pairwise_dhash(frames: List[Dict[str, Any]]) -> float:
    if len(frames) < 2:
        return 0.0
    distances = []
    for index, first in enumerate(frames):
        for second in frames[index + 1 :]:
            distances.append(
                dhash_distance(
                    first["analysis"]["fingerprint"]["dhash"],
                    second["analysis"]["fingerprint"]["dhash"],
                )
                / 64.0
            )
    return round(mean(distances), 4)


def _capture_consistency(frames: List[Dict[str, Any]]) -> float:
    """Score capture consistency, not object identity or condition stability."""

    if len(frames) < 2:
        return 1.0
    vectors = [frame["analysis"]["fingerprint"]["feature_vector"] for frame in frames]
    dimensions = list(zip(*vectors))
    deviations = []
    for values in dimensions:
        average = mean(values)
        variance = mean((value - average) ** 2 for value in values)
        deviations.append(math.sqrt(variance))
    return round(max(0.0, 1.0 - min(1.0, mean(deviations) * 3.0)), 4)


def _representative_ids(frames: List[Dict[str, Any]], limit: int = 3) -> List[str]:
    usable = [
        frame
        for frame in frames
        if frame["duplicate_of"] is None
        and frame["analysis"]["quality_gate"]["passed"]
    ]
    usable.sort(key=lambda item: item["timestamp_ms"])
    if len(usable) <= limit:
        return [item["id"] for item in usable]

    # Earliest / temporal midpoint / latest creates an intelligible filmstrip
    # while keeping expensive multimodal inference bounded.
    chosen = [usable[0], usable[-1]]
    midpoint = (usable[0]["timestamp_ms"] + usable[-1]["timestamp_ms"]) / 2.0
    middle = min(
        usable[1:-1],
        key=lambda item: (
            abs(item["timestamp_ms"] - midpoint),
            -float(item["quality_score"]),
        ),
    )
    chosen.append(middle)
    chosen.sort(key=lambda item: item["timestamp_ms"])
    return [item["id"] for item in chosen[:limit]]


def summarize_frames(
    frames: Iterable[Dict[str, Any]],
    *,
    duration_ms: int,
    duplicate_distance: int = 4,
) -> Dict[str, Any]:
    """Deduplicate and summarize decoded video frames deterministically."""

    ordered = sorted(frames, key=lambda item: (item["timestamp_ms"], item["id"]))
    unique: List[Dict[str, Any]] = []
    for frame in ordered:
        frame["quality_score"] = _quality_score(frame)
        current_hash = frame["analysis"]["fingerprint"]["dhash"]
        nearest: Optional[Tuple[Dict[str, Any], int]] = None
        for candidate in unique:
            distance = dhash_distance(
                current_hash, candidate["analysis"]["fingerprint"]["dhash"]
            )
            if nearest is None or distance < nearest[1]:
                nearest = (candidate, distance)
        if nearest is not None and nearest[1] <= duplicate_distance:
            frame["duplicate_of"] = nearest[0]["id"]
            frame["duplicate_distance"] = nearest[1]
        else:
            frame["duplicate_of"] = None
            frame["duplicate_distance"] = None
            unique.append(frame)

    representative_ids = _representative_ids(ordered)
    representative_set = set(representative_ids)
    for frame in ordered:
        frame["selected"] = frame["id"] in representative_set
        if frame["duplicate_of"] is not None:
            frame["admission_status"] = "DUPLICATE_SUPPRESSED"
        elif not frame["analysis"]["quality_gate"]["passed"]:
            frame["admission_status"] = "QUALITY_REJECTED"
        else:
            frame["admission_status"] = "ACCEPTED"

    usable = [item for item in ordered if item["admission_status"] == "ACCEPTED"]
    if len(ordered) >= 2:
        temporal_span = max(item["timestamp_ms"] for item in ordered) - min(
            item["timestamp_ms"] for item in ordered
        )
    else:
        temporal_span = 0
    temporal_span_ratio = min(1.0, temporal_span / max(duration_ms, 1))
    diversity = _mean_pairwise_dhash(usable)
    checks = {
        "minimum_usable_frames": len(usable) >= 3,
        "temporal_coverage": temporal_span_ratio >= 0.55,
        "viewpoint_diversity": diversity >= 0.015,
        "representative_frames": len(representative_ids) >= 2,
    }
    return {
        "frames": ordered,
        "representative_frame_ids": representative_ids,
        "summary": {
            "requested_frame_count": len(ordered),
            "usable_frame_count": len(usable),
            "quality_rejected_count": sum(
                item["admission_status"] == "QUALITY_REJECTED" for item in ordered
            ),
            "duplicate_suppressed_count": sum(
                item["admission_status"] == "DUPLICATE_SUPPRESSED"
                for item in ordered
            ),
            "representative_frame_count": len(representative_ids),
            "temporal_span_ratio": round(temporal_span_ratio, 4),
            "viewpoint_diversity_score": diversity,
            "capture_consistency_score": _capture_consistency(usable),
        },
        "quality_gate": {
            "passed": all(checks.values()),
            "checks": checks,
            "failed_checks": [key for key, value in checks.items() if not value],
        },
    }


def next_best_observations(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return operational next-capture advice without making object claims."""

    checks = summary["quality_gate"]["checks"]
    metrics = summary["summary"]
    recommendations: List[Dict[str, Any]] = []
    if not checks["minimum_usable_frames"]:
        recommendations.append(
            {
                "id": "OBS-RGB-QUALITY",
                "priority": 1,
                "label": "补拍稳定的可见光全景",
                "reason": "可用帧不足；使用三脚架、漫射光与固定曝光重新采集。",
                "modality": "RGB",
                "risk_class": "NON_CONTACT",
            }
        )
    if not checks["temporal_coverage"] or not checks["viewpoint_diversity"]:
        recommendations.append(
            {
                "id": "OBS-RGB-ROTATION",
                "priority": 2,
                "label": "补齐器物环绕与底足视角",
                "reason": "当前视频的时间覆盖或视角变化不足，建议补拍口沿、腹部、底足与款识。",
                "modality": "RGB_VIDEO",
                "risk_class": "NON_CONTACT",
            }
        )
    if float(metrics.get("capture_consistency_score", 0.0)) < 0.65:
        recommendations.append(
            {
                "id": "OBS-CALIBRATION",
                "priority": 3,
                "label": "加入色卡、比例尺与光照校准帧",
                "reason": "跨帧采集条件波动较大，校准参照有助于区分拍摄变化与表面差异。",
                "modality": "RGB_CALIBRATION",
                "risk_class": "NON_CONTACT",
            }
        )
    recommendations.extend(
        [
            {
                "id": "OBS-UV-NIR",
                "priority": 4,
                "label": "升级至 UV / NIR 表面响应观察",
                "reason": "可在不接触器物的前提下复核可见光候选区域；结果仍需质量门控。",
                "modality": "UV_NIR",
                "risk_class": "NON_CONTACT_CONTROLLED_LIGHT",
            },
            {
                "id": "OBS-MATERIAL-SCIENCE",
                "priority": 5,
                "label": "由专家审批材料科学检测",
                "reason": "图像与视频不能给出元素、分子结构、内部结构或热释光信息；可按风险预算评估 HSI、Raman、XRF、X-ray/CT 或 TL。",
                "modality": "SCIENTIFIC_INSTRUMENT",
                "risk_class": "EXPERT_APPROVAL_REQUIRED",
            },
        ]
    )
    return recommendations
