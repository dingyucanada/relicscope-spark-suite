from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "import-private-artwork-archive.py"
SPEC = importlib.util.spec_from_file_location("private_artwork_archive", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
IMPORTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = IMPORTER
SPEC.loader.exec_module(IMPORTER)


class _UnflaggedUtf8ZipInfo(zipfile.ZipInfo):
    """Write raw UTF-8 names while deliberately omitting ZIP's EFS flag."""

    def __init__(self, filename: str) -> None:
        self._raw_utf8_name = filename.encode("utf-8")
        super().__init__(self._raw_utf8_name.decode("cp437"))
        self.compress_type = zipfile.ZIP_DEFLATED
        self.create_system = 3
        self.external_attr = (stat.S_IFREG | 0o600) << 16

    def _encodeFilenameFlags(self) -> tuple[bytes, int]:  # noqa: N802
        return self._raw_utf8_name, self.flag_bits & ~0x800


def _png_bytes(*, width: int = 24, height: int = 18) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), (28, 91, 74)).save(output, format="PNG")
    return output.getvalue()


def _jpeg_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (20, 16), (210, 205, 190)).save(output, format="JPEG")
    return output.getvalue()


def _pdf_bytes() -> bytes:
    return b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


def _write_zip(path: Path, entries: list[tuple[str | zipfile.ZipInfo, bytes]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)


def _valid_private_archive(path: Path) -> None:
    _write_zip(
        path,
        [
            (
                _UnflaggedUtf8ZipInfo("artifact-group-001 /测试正面 (1).png"),
                _png_bytes(),
            ),
            ("artifact-group-001 /detail (2).jpg", _jpeg_bytes()),
            ("AI清單.pdf", _pdf_bytes()),
            ("__MACOSX/artifact-group-001 /._测试正面 (1).png", b"appledouble"),
            ("artifact-group-001 /.DS_Store", b"finder metadata"),
        ],
    )


def test_default_audit_recovers_unflagged_utf8_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "private.zip"
    output_root = tmp_path / "must-not-exist"
    _valid_private_archive(archive)

    assert (
        IMPORTER.main(
            [str(archive), "--output-root", str(output_root)]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)

    assert result["mode"] == "AUDIT"
    assert result["status"] == "VALIDATED"
    assert result["summary"] == {
        "asset_count": 3,
        "declared_compressed_bytes": result["summary"][
            "declared_compressed_bytes"
        ],
        "declared_uncompressed_bytes": result["summary"][
            "declared_uncompressed_bytes"
        ],
        "directory_count": 0,
        "ignored_metadata_count": 2,
        "image_count": 2,
        "pdf_count": 1,
        "entry_count": 5,
        "validated_asset_bytes": result["summary"]["validated_asset_bytes"],
    }
    recovered = next(
        asset for asset in result["assets"] if "测试正面" in asset["original_path"]
    )
    assert recovered["path_encoding"] == "UTF8_WITHOUT_EFS_RECOVERED"
    assert recovered["candidate_group"] == "artifact-group-001"
    assert recovered["candidate_group_provenance"] == {
        "source": "archive_directory_name",
        "verification_status": "UNVERIFIED",
        "confidence": "LOW",
    }
    assert recovered["view_role"] == "unclassified"
    assert recovered["view_role_provenance"]["confidence"] == "NONE"
    assert not output_root.exists()


def test_import_uses_content_addressing_and_writes_review_materials(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "private.zip"
    output_root = tmp_path / "runtime" / "private-artworks"
    _valid_private_archive(archive)
    audit = IMPORTER.audit_archive(archive)

    destination = IMPORTER.import_validated_archive(
        audit,
        batch_id="customer-test-001",
        output_root=output_root,
    )

    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "IMPORTED"
    assert manifest["batch_id"] == "customer-test-001"
    assert manifest["data_classification"] == "PRIVATE_CONTROLLED"
    assert "archive_path" not in manifest["source"]
    assert manifest["source"]["archive_sha256"] == hashlib.sha256(
        archive.read_bytes()
    ).hexdigest()
    for asset in manifest["assets"]:
        relative = Path(asset["storage_path"])
        assert relative.parts[:2] == ("objects", "sha256")
        blob = destination / relative
        assert blob.is_file()
        assert hashlib.sha256(blob.read_bytes()).hexdigest() == asset["blob_sha256"]
        assert blob.name.startswith(asset["blob_sha256"])
        assert asset["scientific_use_status"].startswith("NOT_APPROVED")

    pdf = next(
        asset
        for asset in manifest["assets"]
        if asset["media_type"] == "application/pdf"
    )
    assert pdf["requires_manual_document_review"] is True
    assert pdf["review_status"] == "REQUIRES_MANUAL_DOCUMENT_REVIEW"
    with (destination / "review.csv").open(encoding="utf-8", newline="") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 3
    assert {row["view_role"] for row in rows} == {"unclassified"}
    assert {row["provenance_confidence"] for row in rows} == {"LOW"}


@pytest.mark.parametrize(
    "unsafe_name, expected",
    [
        ("../escape.png", "path traversal"),
        ("/absolute.png", "absolute entry path"),
        ("C:\\outside.png", "absolute entry path"),
        ("group\\..\\escape.png", "path traversal"),
    ],
)
def test_unsafe_paths_are_rejected(
    tmp_path: Path, unsafe_name: str, expected: str
) -> None:
    archive = tmp_path / "unsafe.zip"
    _write_zip(archive, [(unsafe_name, _png_bytes())])

    with pytest.raises(IMPORTER.ArchivePolicyError, match=expected):
        IMPORTER.audit_archive(archive)


@pytest.mark.parametrize(
    "mode, expected",
    [
        (stat.S_IFLNK | 0o777, "symbolic link"),
        (stat.S_IFREG | 0o755, "executable entry"),
        (stat.S_IFIFO | 0o600, "special file"),
    ],
)
def test_links_executables_and_special_files_are_rejected(
    tmp_path: Path, mode: int, expected: str
) -> None:
    archive = tmp_path / "unsafe-kind.zip"
    info = zipfile.ZipInfo("group/unsafe.png")
    info.create_system = 3
    info.external_attr = mode << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    _write_zip(archive, [(info, _png_bytes())])

    with pytest.raises(IMPORTER.ArchivePolicyError, match=expected):
        IMPORTER.audit_archive(archive)


@pytest.mark.parametrize(
    "name, content, expected",
    [
        ("group/payload.zip", b"PK\x03\x04payload", "nested archive entry"),
        ("group/disguised.png", b"PK\x03\x04payload", "nested archive content"),
        ("group/readme.txt", b"text", "unsupported file type"),
        ("group/fake.jpg", _png_bytes(), "JPEG magic mismatch"),
        ("group/program.png", b"\x7fELFpayload", "executable content"),
    ],
)
def test_nested_archives_unsupported_types_and_spoofed_magic_are_rejected(
    tmp_path: Path, name: str, content: bytes, expected: str
) -> None:
    archive = tmp_path / "unsafe-content.zip"
    _write_zip(archive, [(name, content)])

    with pytest.raises(IMPORTER.ArchivePolicyError, match=expected):
        IMPORTER.audit_archive(archive)


def test_compression_ratio_and_image_pixel_limits_are_enforced(
    tmp_path: Path,
) -> None:
    compressed = tmp_path / "compressed.zip"
    _write_zip(compressed, [("group/image.png", b"0" * 100_000)])
    with pytest.raises(IMPORTER.ArchivePolicyError, match="compression-ratio"):
        IMPORTER.audit_archive(
            compressed,
            limits=IMPORTER.Limits(max_compression_ratio=5.0),
        )

    oversized = tmp_path / "oversized-pixels.zip"
    _write_zip(oversized, [("group/image.png", _png_bytes(width=20, height=20))])
    with pytest.raises(IMPORTER.ArchivePolicyError, match="pixel limit"):
        IMPORTER.audit_archive(
            oversized,
            limits=IMPORTER.Limits(max_image_pixels=399),
        )


def test_rejected_import_does_not_create_output_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = tmp_path / "bad.zip"
    output_root = tmp_path / "private-output"
    _write_zip(archive, [("../escape.png", _png_bytes())])

    assert (
        IMPORTER.main(
            [
                str(archive),
                "--import-batch",
                "unsafe-test",
                "--output-root",
                str(output_root),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().err)["status"] == "REJECTED"
    assert not output_root.exists()
