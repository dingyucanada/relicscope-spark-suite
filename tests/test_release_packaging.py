from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str, cwd: Path):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)


def _minimal_repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    for relative_path in (
        "deploy/package.sh",
        "deploy/v2-nim-list-profiles.sh",
        "deploy/v2-nim-prepare-online.sh",
        "deploy/v2-nim-preflight.sh",
        ".env.v2.nim.example",
        "compose.v2.nim.yml",
    ):
        source = PROJECT_ROOT / relative_path
        destination = repo / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    (repo / "README.md").write_text("# release fixture\n", encoding="utf-8")
    (repo / ".gitignore").write_text("runtime/\nsecrets/\n", encoding="utf-8")
    assert _run("git", "init", cwd=repo).returncode == 0
    assert (
        _run("git", "config", "user.email", "test@example.invalid", cwd=repo).returncode
        == 0
    )
    assert (
        _run("git", "config", "user.name", "RelicScope Test", cwd=repo).returncode == 0
    )
    assert _run("git", "add", ".", cwd=repo).returncode == 0
    assert _run("git", "commit", "-m", "fixture", cwd=repo).returncode == 0
    return repo


def test_clean_release_is_archived_from_git_commit_with_matching_sidecars(tmp_path):
    repo = _minimal_repository(tmp_path)
    (repo / "runtime").mkdir()
    (repo / "runtime/ignored.txt").write_text("runtime evidence", encoding="utf-8")
    (repo / "secrets").mkdir()
    (repo / "secrets/key").write_text("secret", encoding="utf-8")
    output = tmp_path / "packages"

    completed = _run(
        "bash",
        "deploy/package.sh",
        "--role",
        "single",
        "--output-dir",
        str(output),
        cwd=repo,
    )
    assert completed.returncode == 0, completed.stderr
    bundles = [path for path in output.glob("*.tar.gz") if path.is_file()]
    assert len(bundles) == 1
    bundle = bundles[0]
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert bundle.with_name(f"{bundle.name}.sha256").read_text() == (
        f"{digest}  {bundle.name}\n"
    )

    with tarfile.open(bundle, "r:gz") as outer:
        manifest_member = next(
            item for item in outer.getmembers() if item.name == "./MANIFEST.txt"
        )
        manifest_bytes = outer.extractfile(manifest_member).read()
        manifest = manifest_bytes.decode("utf-8")
        release_member = next(
            item
            for item in outer.getmembers()
            if "relicscope-release-" in item.name and item.name.endswith(".tar.gz")
        )
        release_bytes = outer.extractfile(release_member).read()
        checksums_member = next(
            item for item in outer.getmembers() if item.name == "./SHA256SUMS"
        )
        checksums = outer.extractfile(checksums_member).read().decode("utf-8")

    expected_inner = {
        Path(release_member.name).name: hashlib.sha256(release_bytes).hexdigest(),
        "MANIFEST.txt": hashlib.sha256(manifest_bytes).hexdigest(),
    }
    observed_inner = {
        filename: digest
        for digest, filename in (line.split("  ", 1) for line in checksums.splitlines())
    }
    assert observed_inner == expected_inner

    head = _run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    assert f"source_commit={head}\n" in manifest
    assert "source_tree_clean=true\n" in manifest
    assert "source_archive=git-object\n" in manifest
    with tarfile.open(fileobj=io.BytesIO(release_bytes), mode="r:gz") as release:
        names = set(release.getnames())
    assert "README.md" in names
    assert not any(name.startswith(("runtime/", "secrets/")) for name in names)


@pytest.mark.parametrize("mutation", ["tracked", "untracked"])
def test_release_packaging_rejects_dirty_or_untracked_source(tmp_path, mutation):
    repo = _minimal_repository(tmp_path)
    if mutation == "tracked":
        (repo / "README.md").write_text("changed\n", encoding="utf-8")
    else:
        (repo / "untracked.py").write_text("print('untracked')\n", encoding="utf-8")
    output = tmp_path / "packages"

    completed = _run(
        "bash",
        "deploy/package.sh",
        "--output-dir",
        str(output),
        cwd=repo,
    )
    assert completed.returncode != 0
    assert "source tree is not clean" in completed.stderr
    assert not list(output.glob("*.tar.gz")) if output.exists() else True
