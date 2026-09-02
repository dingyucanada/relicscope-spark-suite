from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKER = PROJECT_ROOT / "scripts" / "check-public-release.py"
PUBLIC_DATA = (
    "data/knowledge_manifest.json",
    "data/reference_library/README.md",
    "data/reference_library/manifest.schema.json",
    "data/reference_library/evaluation-manifest.schema.json",
)


def _run_checker(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "scripts/check-public-release.py"), "--repo", str(repo)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _policy_repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(CHECKER, repo / "scripts/check-public-release.py")
    for filename in (".gitignore", ".dockerignore", "Dockerfile"):
        shutil.copy2(PROJECT_ROOT / filename, repo / filename)
    for relative in PUBLIC_DATA:
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / relative, destination)
    completed = subprocess.run(
        ["git", "init"], cwd=repo, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    completed = subprocess.run(
        ["git", "add", "."], cwd=repo, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    return repo


def _add_synthetic_demo(repo: Path, *, extra_media: bool = False) -> None:
    shutil.copytree(PROJECT_ROOT / "demo_media", repo / "demo_media")
    if extra_media:
        (repo / "demo_media/undeclared.jpg").write_bytes(b"not declared")
    completed = subprocess.run(
        ["git", "add", "demo_media"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_current_repository_passes_public_release_boundary():
    completed = _run_checker(PROJECT_ROOT)
    assert completed.returncode == 0, completed.stderr
    assert "historical paths" in completed.stdout


def test_explicit_synthetic_demo_media_is_allowed(tmp_path):
    repo = _policy_repository(tmp_path)
    _add_synthetic_demo(repo)

    completed = _run_checker(repo)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "relative,payload",
    [
        ("data/reference_library/customer.jpg", b"customer media"),
        ("data/reference_library/manifest.json", b"{}"),
        ("data/test-packs/customer-batch/manifest.json", b"{}"),
        ("runtime/v2-data/scout.sqlite3", b"database"),
        ("customer-uploads/object.jpg", b"customer media"),
        ("customer-photo.avif", b"customer media"),
        ("customer-export.tar", b"archive"),
        ("customer-export.tgz", b"archive"),
        ("customer-export.rar", b"archive"),
        ("customer-export.gz", b"archive"),
        ("customer-export.zst", b"archive"),
        (".env", b"PRIVATE_VALUE=redacted"),
        ("config/.env", b"PRIVATE_VALUE=redacted"),
        ("secrets/device-token", b"redacted"),
        ("deploy/private-artworks/device-token", b"redacted"),
    ],
)
def test_force_added_controlled_or_raw_data_is_rejected(tmp_path, relative, payload):
    repo = _policy_repository(tmp_path)
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    completed = subprocess.run(
        ["git", "add", "--force", relative],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    audited = _run_checker(repo)

    assert audited.returncode == 1
    assert relative in audited.stderr


def test_undeclared_demo_media_is_rejected(tmp_path):
    repo = _policy_repository(tmp_path)
    _add_synthetic_demo(repo, extra_media=True)

    completed = _run_checker(repo)

    assert completed.returncode == 1
    assert "demo_media/undeclared.jpg" in completed.stderr


def test_allowlisted_synthetic_evidence_is_hash_pinned(tmp_path):
    repo = _policy_repository(tmp_path)
    evidence = repo / "docs/evidence/ui-desktop-initial.png"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"replacement bytes require explicit review")
    completed = subprocess.run(
        ["git", "add", "docs/evidence/ui-desktop-initial.png"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    audited = _run_checker(repo)

    assert audited.returncode == 1
    assert "changed without allowlist review" in audited.stderr


def test_staged_synthetic_evidence_cannot_hide_behind_clean_worktree(tmp_path):
    repo = _policy_repository(tmp_path)
    evidence = repo / "docs/evidence/ui-desktop-initial.png"
    evidence.parent.mkdir(parents=True)
    approved = PROJECT_ROOT / "docs/evidence/ui-desktop-initial.png"
    shutil.copy2(approved, evidence)
    subprocess.run(
        ["git", "add", "docs/evidence/ui-desktop-initial.png"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    evidence.write_bytes(b"staged private replacement")
    subprocess.run(
        ["git", "add", "docs/evidence/ui-desktop-initial.png"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    shutil.copy2(approved, evidence)

    audited = _run_checker(repo)

    assert audited.returncode == 1
    assert "staged public file changed" in audited.stderr


def test_synthetic_demo_checksum_file_is_validated(tmp_path):
    repo = _policy_repository(tmp_path)
    _add_synthetic_demo(repo)
    checksum = repo / "demo_media/SHA256SUMS"
    checksum.write_text("0" * 64 + "  reference.png\n", encoding="utf-8")

    audited = _run_checker(repo)

    assert audited.returncode == 1
    assert "checksum file differs" in audited.stderr


def test_synthetic_label_cannot_replace_reviewed_demo_bytes(tmp_path):
    repo = _policy_repository(tmp_path)
    _add_synthetic_demo(repo)
    media = repo / "demo_media/reference.png"
    media.write_bytes(b"arbitrary media relabelled as synthetic")
    digest = hashlib.sha256(media.read_bytes()).hexdigest()
    manifest_path = repo / "demo_media/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        if entry["path"] == "reference.png":
            entry["sha256"] = digest
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    checksum_path = repo / "demo_media/SHA256SUMS"
    checksum_lines = []
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        checksum_lines.append(
            f"{digest}  reference.png" if line.endswith("  reference.png") else line
        )
    checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    audited = _run_checker(repo)

    assert audited.returncode == 1
    assert "without allowlist review" in audited.stderr


def test_dangling_allowlisted_symlink_is_rejected(tmp_path):
    repo = _policy_repository(tmp_path)
    target = repo / "data/knowledge_manifest.json"
    target.unlink()
    target.symlink_to("missing.json")
    subprocess.run(
        ["git", "add", "--force", "data/knowledge_manifest.json"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    audited = _run_checker(repo)

    assert audited.returncode == 1
    assert "may not be a symlink" in audited.stderr


def test_nim_public_environment_template_is_not_ignored(tmp_path):
    repo = _policy_repository(tmp_path)
    shutil.copy2(PROJECT_ROOT / ".env.v2.nim.example", repo / ".env.v2.nim.example")
    added = subprocess.run(
        ["git", "add", ".env.v2.nim.example"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert added.returncode == 0, added.stderr

    audited = _run_checker(repo)

    assert audited.returncode == 0, audited.stderr


def test_deleted_controlled_data_remains_blocked_from_git_history(tmp_path):
    repo = _policy_repository(tmp_path)

    def commit(message: str) -> None:
        completed = subprocess.run(
            [
                "git",
                "-c",
                "user.name=Release Test",
                "-c",
                "user.email=release-test@example.invalid",
                "commit",
                "-m",
                message,
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

    commit("safe baseline")
    historical = repo / "data/reference_library/customer.jpg"
    historical.write_bytes(b"historical customer media")
    subprocess.run(
        ["git", "add", "--force", "data/reference_library/customer.jpg"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    commit("unsafe historical fixture")
    subprocess.run(
        ["git", "rm", "data/reference_library/customer.jpg"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    commit("remove fixture from tip")

    audited = _run_checker(repo)

    assert audited.returncode == 1
    assert "Git history" in audited.stderr
    assert "data/reference_library/customer.jpg" in audited.stderr


@pytest.mark.parametrize(
    "filename,unsafe_line,expected",
    [
        (".gitignore", "!data/**\n", "unapproved allow rule"),
        (".gitignore", "!data\n", "unapproved allow rule"),
        (".dockerignore", "!data/**\n", "unapproved allow rule"),
        (".dockerignore", "!data\n", "unapproved allow rule"),
        (".dockerignore", "!runtime/**\n", "unapproved allow rule"),
        (".dockerignore", "!app/**\n", "must follow every source allow rule"),
        ("Dockerfile", "COPY data /opt/relicscope/leaked-data\n", "whole data tree"),
    ],
)
def test_release_policy_cannot_be_broadened(tmp_path, filename, unsafe_line, expected):
    repo = _policy_repository(tmp_path)
    policy = repo / filename
    policy.write_text(policy.read_text(encoding="utf-8") + "\n" + unsafe_line, encoding="utf-8")

    completed = _run_checker(repo)

    assert completed.returncode == 1
    assert expected in completed.stderr
