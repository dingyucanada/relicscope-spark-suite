#!/usr/bin/env python3
"""Audit or safely import an operator-supplied private artwork ZIP archive.

Audit is the default and never creates files. Import is explicit and writes only
content-addressed blobs plus review metadata under a private runtime directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import struct
import sys
import unicodedata
import warnings
import zipfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

from PIL import Image, UnidentifiedImageError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "runtime" / "private-artworks"
SCHEMA_VERSION = "relicscope-private-artwork-import-v1"
CHUNK_BYTES = 1024 * 1024
PREFIX_BYTES = 560
UTF8_FLAG = 0x800
ENCRYPTED_FLAG = 0x1
UNICODE_PATH_EXTRA_ID = 0x7075

ALLOWED_MEDIA = {
    ".png": ("image/png", ".png", "PNG"),
    ".jpg": ("image/jpeg", ".jpg", "JPEG"),
    ".jpeg": ("image/jpeg", ".jpg", "JPEG"),
    ".pdf": ("application/pdf", ".pdf", "PDF"),
}
NESTED_ARCHIVE_SUFFIXES = {
    ".7z",
    ".bz2",
    ".cab",
    ".gz",
    ".iso",
    ".jar",
    ".rar",
    ".tar",
    ".tgz",
    ".txz",
    ".xz",
    ".zip",
    ".zst",
}
EXECUTABLE_SUFFIXES = {
    ".app",
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".dylib",
    ".exe",
    ".js",
    ".msi",
    ".ps1",
    ".py",
    ".sh",
    ".so",
}
FORBIDDEN_BIDI = {
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
    "\u2066",
    "\u2067",
    "\u2068",
    "\u2069",
}
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
BATCH_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class ArchivePolicyError(ValueError):
    """The archive violates the bounded private-data intake policy."""


@dataclass(frozen=True)
class Limits:
    max_archive_bytes: int = 2 * 1024 * 1024 * 1024
    max_entries: int = 2_000
    max_entry_bytes: int = 128 * 1024 * 1024
    max_total_bytes: int = 4 * 1024 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_image_pixels: int = 60_000_000

    def validate(self) -> None:
        numeric = {
            "max_archive_bytes": self.max_archive_bytes,
            "max_entries": self.max_entries,
            "max_entry_bytes": self.max_entry_bytes,
            "max_total_bytes": self.max_total_bytes,
            "max_image_pixels": self.max_image_pixels,
        }
        for name, value in numeric.items():
            if value < 1:
                raise ArchivePolicyError(f"{name} must be positive")
        if self.max_compression_ratio < 1.0:
            raise ArchivePolicyError("max_compression_ratio must be at least 1.0")


@dataclass(frozen=True)
class DecodedPath:
    original: str
    normalized: str
    parts: tuple[str, ...]
    encoding: str


@dataclass(frozen=True)
class ValidatedAsset:
    entry_index: int
    original_path: str
    normalized_path: str
    path_encoding: str
    media_type: str
    storage_extension: str
    byte_size: int
    sha256: str
    candidate_group: str | None
    sequence_hint: int | None
    archive_timestamp_claim: str
    image: dict[str, object] | None

    def manifest_record(self, storage_path: str | None = None) -> dict[str, object]:
        manual_document_review = self.media_type == "application/pdf"
        record: dict[str, object] = {
            "asset_id": f"asset:{self.sha256}:{self.entry_index}",
            "blob_sha256": self.sha256,
            "media_type": self.media_type,
            "byte_size": self.byte_size,
            "original_path": self.original_path,
            "normalized_path": self.normalized_path,
            "path_encoding": self.path_encoding,
            "storage_path": storage_path,
            "candidate_group": self.candidate_group,
            "candidate_group_provenance": {
                "source": "archive_directory_name",
                "verification_status": "UNVERIFIED",
                "confidence": "LOW" if self.candidate_group else "NONE",
            },
            "sequence_hint": self.sequence_hint,
            "sequence_hint_provenance": {
                "source": "filename_parenthesized_number",
                "verification_status": "UNVERIFIED",
                "confidence": "LOW" if self.sequence_hint is not None else "NONE",
            },
            "view_role": "unclassified",
            "view_role_provenance": {
                "source": "not_inferred",
                "verification_status": "REQUIRES_HUMAN_LABEL",
                "confidence": "NONE",
            },
            "archive_timestamp_claim": self.archive_timestamp_claim,
            "provenance": {
                "source_type": "OPERATOR_SUPPLIED_PRIVATE_ARCHIVE",
                "verification_status": "UNVERIFIED",
                "confidence": "LOW",
            },
            "requires_manual_document_review": manual_document_review,
            "review_status": (
                "REQUIRES_MANUAL_DOCUMENT_REVIEW"
                if manual_document_review
                else "PENDING_ARTIFACT_AND_VIEW_REVIEW"
            ),
            "scientific_use_status": "NOT_APPROVED_FOR_IDENTITY_OR_AUTHENTICITY_CLAIMS",
        }
        if self.image is not None:
            record["image"] = self.image
        return record


@dataclass(frozen=True)
class AuditResult:
    archive_path: Path
    archive_filename: str
    archive_sha256: str
    archive_byte_size: int
    entry_count: int
    directory_count: int
    ignored_entries: tuple[dict[str, str], ...]
    assets: tuple[ValidatedAsset, ...]
    declared_uncompressed_bytes: int
    declared_compressed_bytes: int
    limits: Limits

    def manifest(
        self,
        *,
        mode: str,
        batch_id: str | None = None,
        storage_paths: dict[int, str] | None = None,
    ) -> dict[str, object]:
        paths = storage_paths or {}
        image_count = sum(a.media_type.startswith("image/") for a in self.assets)
        pdf_count = sum(a.media_type == "application/pdf" for a in self.assets)
        asset_records = [
            asset.manifest_record(paths.get(asset.entry_index))
            for asset in self.assets
        ]
        for record in asset_records:
            record["provenance"]["archive_sha256"] = self.archive_sha256
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "VALIDATED" if mode == "AUDIT" else "IMPORTED",
            "mode": mode,
            "batch_id": batch_id,
            "generated_at": _utc_now(),
            "data_classification": "PRIVATE_CONTROLLED",
            "source": {
                "source_type": "OPERATOR_SUPPLIED_PRIVATE_ARCHIVE",
                "archive_filename": self.archive_filename,
                "archive_sha256": self.archive_sha256,
                "archive_byte_size": self.archive_byte_size,
                "verification_status": "UNVERIFIED_OPERATOR_SUPPLIED",
                "confidence": "LOW",
            },
            "limits": {
                "max_archive_bytes": self.limits.max_archive_bytes,
                "max_entries": self.limits.max_entries,
                "max_entry_bytes": self.limits.max_entry_bytes,
                "max_total_bytes": self.limits.max_total_bytes,
                "max_compression_ratio": self.limits.max_compression_ratio,
                "max_image_pixels": self.limits.max_image_pixels,
            },
            "summary": {
                "entry_count": self.entry_count,
                "directory_count": self.directory_count,
                "asset_count": len(self.assets),
                "image_count": image_count,
                "pdf_count": pdf_count,
                "ignored_metadata_count": len(self.ignored_entries),
                "declared_uncompressed_bytes": self.declared_uncompressed_bytes,
                "declared_compressed_bytes": self.declared_compressed_bytes,
                "validated_asset_bytes": sum(a.byte_size for a in self.assets),
            },
            "security": {
                "verdict": "PASS",
                "path_traversal": "NOT_FOUND",
                "absolute_paths": "NOT_FOUND",
                "symbolic_links": "NOT_FOUND",
                "special_files": "NOT_FOUND",
                "executables": "NOT_FOUND",
                "nested_archives": "NOT_FOUND",
                "encrypted_entries": "NOT_FOUND",
                "content_magic": "VALIDATED",
                "images": "PILLOW_DECODED_WITH_PIXEL_LIMIT",
                "pdfs": "MAGIC_VALIDATED_REQUIRES_MANUAL_REVIEW",
            },
            "assets": asset_records,
            "ignored_entries": list(self.ignored_entries),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _open_regular_archive(path: Path) -> Iterator[BinaryIO]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ArchivePolicyError(f"cannot stat archive: {exc}") from exc
    if stat.S_ISLNK(before.st_mode):
        raise ArchivePolicyError("archive path must not be a symbolic link")
    if not stat.S_ISREG(before.st_mode):
        raise ArchivePolicyError("archive path must be a regular file")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ArchivePolicyError(f"cannot open archive safely: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ArchivePolicyError("opened archive is not a regular file")
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ArchivePolicyError("archive changed while it was being opened")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            yield stream
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _hash_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


def _unicode_path_extra(info: zipfile.ZipInfo, raw_name: bytes) -> str | None:
    offset = 0
    extra = info.extra
    while offset + 4 <= len(extra):
        header_id, size = struct.unpack_from("<HH", extra, offset)
        offset += 4
        value = extra[offset : offset + size]
        offset += size
        if header_id != UNICODE_PATH_EXTRA_ID or len(value) < 5 or value[0] != 1:
            continue
        expected_crc = struct.unpack_from("<I", value, 1)[0]
        if expected_crc != zlib.crc32(raw_name) & 0xFFFFFFFF:
            continue
        try:
            return value[5:].decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            continue
    return None


def _decode_filename(info: zipfile.ZipInfo) -> tuple[str, str]:
    if info.flag_bits & UTF8_FLAG:
        return unicodedata.normalize("NFC", info.filename), "UTF8_EFS"
    try:
        raw_name = info.filename.encode("cp437", errors="strict")
    except UnicodeEncodeError:
        return unicodedata.normalize("NFC", info.filename), "ZIP_DECODER_FALLBACK"
    from_extra = _unicode_path_extra(info, raw_name)
    if from_extra is not None:
        return unicodedata.normalize("NFC", from_extra), "UNICODE_PATH_EXTRA"
    if all(byte < 128 for byte in raw_name):
        return info.filename, "ASCII"
    try:
        recovered = raw_name.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return unicodedata.normalize("NFC", info.filename), "CP437_FALLBACK"
    return unicodedata.normalize("NFC", recovered), "UTF8_WITHOUT_EFS_RECOVERED"


def _clean_component(component: str, original_path: str) -> str:
    if any(character in FORBIDDEN_BIDI for character in component):
        raise ArchivePolicyError(f"bidirectional control in entry path: {original_path!r}")
    if any(unicodedata.category(character) == "Cc" for character in component):
        raise ArchivePolicyError(f"control character in entry path: {original_path!r}")
    cleaned = unicodedata.normalize("NFC", component).strip().rstrip(".")
    if cleaned in {"", ".", ".."}:
        raise ArchivePolicyError(f"unsafe path component in entry: {original_path!r}")
    if len(cleaned) > 255:
        raise ArchivePolicyError(f"path component is too long: {original_path!r}")
    return cleaned


def _decode_and_validate_path(info: zipfile.ZipInfo) -> DecodedPath:
    original, encoding = _decode_filename(info)
    if not original or len(original) > 1_024:
        raise ArchivePolicyError("entry path is empty or exceeds 1024 characters")
    if original.startswith(("/", "\\")) or WINDOWS_DRIVE.match(original):
        raise ArchivePolicyError(f"absolute entry path is forbidden: {original!r}")

    slash_path = original.replace("\\", "/")
    if slash_path.startswith("//"):
        raise ArchivePolicyError(f"UNC entry path is forbidden: {original!r}")
    raw_parts = slash_path.split("/")
    if info.is_dir() and raw_parts and raw_parts[-1] == "":
        raw_parts.pop()
    if not raw_parts or any(part == "" for part in raw_parts):
        raise ArchivePolicyError(f"empty path component in entry: {original!r}")
    if any(part in {".", ".."} for part in raw_parts):
        raise ArchivePolicyError(f"path traversal is forbidden: {original!r}")
    parts = tuple(_clean_component(part, original) for part in raw_parts)
    return DecodedPath(
        original=original,
        normalized="/".join(parts),
        parts=parts,
        encoding=encoding,
    )


def _entry_mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) & 0xFFFF


def _validate_entry_kind(info: zipfile.ZipInfo, path: DecodedPath) -> None:
    if info.flag_bits & ENCRYPTED_FLAG:
        raise ArchivePolicyError(f"encrypted entry is forbidden: {path.original!r}")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise ArchivePolicyError(
            f"unsupported compression method for entry: {path.original!r}"
        )
    mode = _entry_mode(info)
    file_type = stat.S_IFMT(mode)
    if file_type == stat.S_IFLNK:
        raise ArchivePolicyError(f"symbolic link is forbidden: {path.original!r}")
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise ArchivePolicyError(f"special file is forbidden: {path.original!r}")
    if not info.is_dir() and file_type == stat.S_IFDIR:
        raise ArchivePolicyError(f"file entry declares directory mode: {path.original!r}")
    if not info.is_dir() and mode & 0o111:
        raise ArchivePolicyError(f"executable entry is forbidden: {path.original!r}")


def _is_macos_metadata(path: DecodedPath) -> bool:
    basename = path.parts[-1]
    return (
        path.parts[0].casefold() == "__macosx"
        or basename.casefold() == ".ds_store"
        or basename.startswith("._")
    )


def _compression_ratio(info: zipfile.ZipInfo) -> float:
    if info.file_size == 0:
        return 1.0
    if info.compress_size == 0:
        return float("inf")
    return info.file_size / info.compress_size


def _sequence_hint(filename: str) -> int | None:
    match = re.search(r"\((\d{1,6})\)(?=\.[^.]+$)", filename)
    return int(match.group(1)) if match else None


def _entry_timestamp(info: zipfile.ZipInfo) -> str:
    try:
        return datetime(*info.date_time).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return "UNAVAILABLE"


def _stream_entry_digest(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    max_entry_bytes: int,
) -> tuple[str, int, bytes]:
    digest = hashlib.sha256()
    prefix = bytearray()
    total = 0
    try:
        with archive.open(info, "r") as source:
            while True:
                chunk = source.read(CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_entry_bytes:
                    raise ArchivePolicyError(
                        f"entry exceeds streamed size limit: {info.filename!r}"
                    )
                digest.update(chunk)
                if len(prefix) < PREFIX_BYTES:
                    prefix.extend(chunk[: PREFIX_BYTES - len(prefix)])
    except (
        EOFError,
        RuntimeError,
        NotImplementedError,
        OSError,
        zipfile.BadZipFile,
        zlib.error,
    ) as exc:
        raise ArchivePolicyError(f"cannot safely read ZIP entry: {exc}") from exc
    if total != info.file_size:
        raise ArchivePolicyError(f"entry size differs from central directory: {info.filename!r}")
    return digest.hexdigest(), total, bytes(prefix)


def _nested_archive_magic(prefix: bytes) -> bool:
    signatures = (
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"PK\x07\x08",
        b"7z\xbc\xaf\x27\x1c",
        b"Rar!\x1a\x07",
        b"\x1f\x8b",
        b"BZh",
        b"\xfd7zXZ\x00",
    )
    return prefix.startswith(signatures) or (
        len(prefix) >= 262 and prefix[257:262] == b"ustar"
    )


def _executable_magic(prefix: bytes) -> bool:
    return prefix.startswith(
        (
            b"\x7fELF",
            b"MZ",
            b"\xcf\xfa\xed\xfe",
            b"\xce\xfa\xed\xfe",
            b"\xfe\xed\xfa\xcf",
            b"\xfe\xed\xfa\xce",
            b"#!",
        )
    )


def _validate_magic(path: DecodedPath, media_type: str, prefix: bytes) -> None:
    if _nested_archive_magic(prefix):
        raise ArchivePolicyError(f"nested archive content is forbidden: {path.original!r}")
    if _executable_magic(prefix):
        raise ArchivePolicyError(f"executable content is forbidden: {path.original!r}")
    if media_type == "image/png" and not prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ArchivePolicyError(f"PNG magic mismatch: {path.original!r}")
    if media_type == "image/jpeg" and not prefix.startswith(b"\xff\xd8\xff"):
        raise ArchivePolicyError(f"JPEG magic mismatch: {path.original!r}")
    if media_type == "application/pdf" and not prefix.startswith(b"%PDF-"):
        raise ArchivePolicyError(f"PDF magic mismatch: {path.original!r}")


def _decode_image(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    expected_format: str,
    max_image_pixels: int,
    display_path: str,
) -> dict[str, object]:
    try:
        with archive.open(info, "r") as source:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(source) as image:
                    width, height = image.size
                    if width < 1 or height < 1:
                        raise ArchivePolicyError(
                            f"image dimensions must be positive: {display_path!r}"
                        )
                    if width * height > max_image_pixels:
                        raise ArchivePolicyError(
                            f"image exceeds pixel limit: {display_path!r}"
                        )
                    if image.format != expected_format:
                        raise ArchivePolicyError(
                            f"image format does not match extension: {display_path!r}"
                        )
                    if getattr(image, "n_frames", 1) != 1:
                        raise ArchivePolicyError(
                            f"multi-frame image is forbidden: {display_path!r}"
                        )
                    image.load()
                    return {
                        "format": image.format,
                        "width": width,
                        "height": height,
                        "pixel_count": width * height,
                        "mode": image.mode,
                    }
    except ArchivePolicyError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        zipfile.BadZipFile,
    ) as exc:
        raise ArchivePolicyError(f"image cannot be safely decoded: {display_path!r}") from exc


def audit_archive(path: Path, *, limits: Limits | None = None) -> AuditResult:
    limits = limits or Limits()
    limits.validate()
    path = Path(path)
    if path.suffix.casefold() != ".zip":
        raise ArchivePolicyError("input must be a .zip archive")

    with _open_regular_archive(path) as source:
        archive_size = os.fstat(source.fileno()).st_size
        if archive_size > limits.max_archive_bytes:
            raise ArchivePolicyError("archive exceeds compressed size limit")
        archive_sha256 = _hash_stream(source)
        source.seek(0)
        try:
            archive = zipfile.ZipFile(source, "r")
        except (
            OSError,
            UnicodeDecodeError,
            ValueError,
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
        ) as exc:
            raise ArchivePolicyError(f"invalid ZIP archive: {exc}") from exc
        with archive:
            entries = archive.infolist()
            if len(entries) > limits.max_entries:
                raise ArchivePolicyError("archive exceeds entry-count limit")
            if not entries:
                raise ArchivePolicyError("archive is empty")

            declared_total = 0
            compressed_total = 0
            directory_count = 0
            decoded_paths: list[DecodedPath] = []
            normalized_seen: dict[str, str] = {}
            for info in entries:
                decoded = _decode_and_validate_path(info)
                _validate_entry_kind(info, decoded)
                decoded_paths.append(decoded)
                key = decoded.normalized.casefold()
                if key in normalized_seen:
                    raise ArchivePolicyError(
                        "duplicate path after normalization: "
                        f"{decoded.original!r} conflicts with {normalized_seen[key]!r}"
                    )
                normalized_seen[key] = decoded.original
                if info.is_dir():
                    directory_count += 1
                    continue
                if info.file_size < 0 or info.compress_size < 0:
                    raise ArchivePolicyError("negative ZIP size metadata is forbidden")
                if info.file_size > limits.max_entry_bytes:
                    raise ArchivePolicyError(
                        f"entry exceeds declared size limit: {decoded.original!r}"
                    )
                if _compression_ratio(info) > limits.max_compression_ratio:
                    raise ArchivePolicyError(
                        f"entry exceeds compression-ratio limit: {decoded.original!r}"
                    )
                declared_total += info.file_size
                compressed_total += info.compress_size
                if declared_total > limits.max_total_bytes:
                    raise ArchivePolicyError("archive exceeds total uncompressed size limit")

            ignored: list[dict[str, str]] = []
            assets: list[ValidatedAsset] = []
            actual_asset_total = 0
            for entry_index, (info, decoded) in enumerate(zip(entries, decoded_paths)):
                if info.is_dir():
                    continue
                if _is_macos_metadata(decoded):
                    ignored.append(
                        {"original_path": decoded.original, "reason": "MACOS_METADATA"}
                    )
                    continue
                suffix = Path(decoded.parts[-1]).suffix.casefold()
                if suffix in NESTED_ARCHIVE_SUFFIXES:
                    raise ArchivePolicyError(
                        f"nested archive entry is forbidden: {decoded.original!r}"
                    )
                if suffix in EXECUTABLE_SUFFIXES:
                    raise ArchivePolicyError(
                        f"executable entry is forbidden: {decoded.original!r}"
                    )
                if suffix not in ALLOWED_MEDIA:
                    raise ArchivePolicyError(
                        f"unsupported file type in archive: {decoded.original!r}"
                    )
                media_type, storage_extension, expected_format = ALLOWED_MEDIA[suffix]
                digest, byte_size, prefix = _stream_entry_digest(
                    archive,
                    info,
                    max_entry_bytes=limits.max_entry_bytes,
                )
                actual_asset_total += byte_size
                if actual_asset_total > limits.max_total_bytes:
                    raise ArchivePolicyError("streamed asset bytes exceed total size limit")
                _validate_magic(decoded, media_type, prefix)
                image = None
                if media_type.startswith("image/"):
                    image = _decode_image(
                        archive,
                        info,
                        expected_format=expected_format,
                        max_image_pixels=limits.max_image_pixels,
                        display_path=decoded.original,
                    )
                candidate_group = decoded.parts[0] if len(decoded.parts) > 1 else None
                assets.append(
                    ValidatedAsset(
                        entry_index=entry_index,
                        original_path=decoded.original,
                        normalized_path=decoded.normalized,
                        path_encoding=decoded.encoding,
                        media_type=media_type,
                        storage_extension=storage_extension,
                        byte_size=byte_size,
                        sha256=digest,
                        candidate_group=candidate_group,
                        sequence_hint=_sequence_hint(decoded.parts[-1]),
                        archive_timestamp_claim=_entry_timestamp(info),
                        image=image,
                    )
                )
            if not assets:
                raise ArchivePolicyError("archive contains no supported artwork assets")

    return AuditResult(
        archive_path=path,
        archive_filename=path.name,
        archive_sha256=archive_sha256,
        archive_byte_size=archive_size,
        entry_count=len(entries),
        directory_count=directory_count,
        ignored_entries=tuple(ignored),
        assets=tuple(assets),
        declared_uncompressed_bytes=declared_total,
        declared_compressed_bytes=compressed_total,
        limits=limits,
    )


def _assert_safe_output_root(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        if not os.path.lexists(current):
            continue
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ArchivePolicyError(f"output path contains a symbolic link: {current}")
        if current == absolute and not stat.S_ISDIR(mode):
            raise ArchivePolicyError("output root exists and is not a directory")


def _write_json(path: Path, value: dict[str, object]) -> None:
    with path.open("x", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2, sort_keys=True)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    path.chmod(0o600)


REVIEW_FIELDS = (
    "asset_id",
    "blob_sha256",
    "media_type",
    "byte_size",
    "candidate_group",
    "candidate_group_confidence",
    "view_role",
    "view_role_confidence",
    "original_path",
    "storage_path",
    "provenance_source",
    "provenance_status",
    "provenance_confidence",
    "requires_manual_document_review",
    "review_status",
    "review_decision",
    "reviewer",
    "review_notes",
)


def _write_review_csv(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("x", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "asset_id": record["asset_id"],
                    "blob_sha256": record["blob_sha256"],
                    "media_type": record["media_type"],
                    "byte_size": record["byte_size"],
                    "candidate_group": record["candidate_group"] or "",
                    "candidate_group_confidence": record[
                        "candidate_group_provenance"
                    ]["confidence"],
                    "view_role": record["view_role"],
                    "view_role_confidence": record["view_role_provenance"][
                        "confidence"
                    ],
                    "original_path": record["original_path"],
                    "storage_path": record["storage_path"],
                    "provenance_source": record["provenance"]["source_type"],
                    "provenance_status": record["provenance"][
                        "verification_status"
                    ],
                    "provenance_confidence": record["provenance"]["confidence"],
                    "requires_manual_document_review": str(
                        record["requires_manual_document_review"]
                    ).lower(),
                    "review_status": record["review_status"],
                    "review_decision": "",
                    "reviewer": "",
                    "review_notes": "",
                }
            )
        target.flush()
        os.fsync(target.fileno())
    path.chmod(0o600)


def _copy_validated_entry(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
    expected: ValidatedAsset,
) -> None:
    temporary = destination.with_name(f".{destination.name}.part")
    digest = hashlib.sha256()
    total = 0
    try:
        with archive.open(info, "r") as source, temporary.open("xb") as target:
            while True:
                chunk = source.read(CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected.byte_size:
                    raise ArchivePolicyError("archive entry changed after validation")
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if total != expected.byte_size or digest.hexdigest() != expected.sha256:
            raise ArchivePolicyError("archive entry changed after validation")
        temporary.chmod(0o600)
        os.replace(temporary, destination)
    finally:
        if os.path.lexists(temporary):
            temporary.unlink()


def import_validated_archive(
    result: AuditResult,
    *,
    batch_id: str,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> Path:
    if not BATCH_PATTERN.fullmatch(batch_id):
        raise ArchivePolicyError(
            "batch id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}"
        )
    output_root = Path(output_root).absolute()
    _assert_safe_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = output_root / batch_id
    if os.path.lexists(destination):
        raise ArchivePolicyError(f"batch destination already exists: {destination}")

    lock_path = output_root / f".import-{batch_id}.lock"
    try:
        lock_descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
    except FileExistsError as exc:
        raise ArchivePolicyError(f"batch import is already in progress: {batch_id}") from exc
    staging = output_root / f".{batch_id}.staging-{secrets.token_hex(8)}"
    try:
        with os.fdopen(lock_descriptor, "w", encoding="utf-8") as lock:
            lock.write(f"pid={os.getpid()}\n")
            lock.flush()
            os.fsync(lock.fileno())
        staging.mkdir(mode=0o700)
        storage_paths: dict[int, str] = {}
        with _open_regular_archive(result.archive_path) as source:
            current_hash = _hash_stream(source)
            if current_hash != result.archive_sha256:
                raise ArchivePolicyError("archive changed after audit")
            source.seek(0)
            with zipfile.ZipFile(source, "r") as archive:
                entries = archive.infolist()
                if len(entries) != result.entry_count:
                    raise ArchivePolicyError("archive entry count changed after audit")
                for asset in result.assets:
                    info = entries[asset.entry_index]
                    decoded = _decode_and_validate_path(info)
                    if decoded.normalized != asset.normalized_path:
                        raise ArchivePolicyError("archive path changed after audit")
                    relative = (
                        Path("objects")
                        / "sha256"
                        / asset.sha256[:2]
                        / f"{asset.sha256}{asset.storage_extension}"
                    )
                    blob = staging / relative
                    blob.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    if not blob.exists():
                        _copy_validated_entry(archive, info, blob, asset)
                    storage_paths[asset.entry_index] = relative.as_posix()

        manifest = result.manifest(
            mode="IMPORT", batch_id=batch_id, storage_paths=storage_paths
        )
        _write_json(staging / "manifest.json", manifest)
        _write_review_csv(staging / "review.csv", manifest["assets"])
        if os.path.lexists(destination):
            raise ArchivePolicyError(f"batch destination appeared during import: {destination}")
        os.rename(staging, destination)
        return destination
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    finally:
        if os.path.lexists(lock_path):
            lock_path.unlink()


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _ratio(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a number") from exc
    if parsed < 1.0:
        raise argparse.ArgumentTypeError("ratio must be at least 1.0")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = Limits()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="private ZIP archive to audit")
    parser.add_argument(
        "--import-batch",
        metavar="BATCH",
        help="explicitly import into runtime/private-artworks/BATCH",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="private import root (default: repo runtime/private-artworks)",
    )
    parser.add_argument(
        "--max-archive-bytes", type=_positive_int, default=defaults.max_archive_bytes
    )
    parser.add_argument("--max-entries", type=_positive_int, default=defaults.max_entries)
    parser.add_argument(
        "--max-entry-bytes", type=_positive_int, default=defaults.max_entry_bytes
    )
    parser.add_argument(
        "--max-total-bytes", type=_positive_int, default=defaults.max_total_bytes
    )
    parser.add_argument(
        "--max-compression-ratio",
        type=_ratio,
        default=defaults.max_compression_ratio,
    )
    parser.add_argument(
        "--max-image-pixels", type=_positive_int, default=defaults.max_image_pixels
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    limits = Limits(
        max_archive_bytes=args.max_archive_bytes,
        max_entries=args.max_entries,
        max_entry_bytes=args.max_entry_bytes,
        max_total_bytes=args.max_total_bytes,
        max_compression_ratio=args.max_compression_ratio,
        max_image_pixels=args.max_image_pixels,
    )
    try:
        result = audit_archive(args.archive, limits=limits)
        if args.import_batch:
            destination = import_validated_archive(
                result,
                batch_id=args.import_batch,
                output_root=args.output_root,
            )
            output = json.loads(
                (destination / "manifest.json").read_text(encoding="utf-8")
            )
            output["destination"] = str(destination)
        else:
            output = result.manifest(mode="AUDIT")
    except (ArchivePolicyError, OSError, zipfile.BadZipFile) as exc:
        print(
            json.dumps(
                {"status": "REJECTED", "error": str(exc)}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
