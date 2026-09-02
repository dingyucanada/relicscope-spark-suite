#!/usr/bin/env python3
"""Fail closed when a public release could include controlled or user data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]

PUBLIC_DATA_SHA256 = {
    "data/knowledge_manifest.json": (
        "f086601fb5a20e1b424e095070b1e4c67d89f909f0565ff629198384f0f3580c"
    ),
    "data/reference_library/README.md": (
        "5865c7e253bda756623f94d29c9cca8510a867584928fefff24f7993d79e2457"
    ),
    "data/reference_library/manifest.schema.json": (
        "7181a2e8cee92a39a2e9971c631a7e0b25435cdd5a2ec3b0879f76ae2a19e86b"
    ),
    "data/reference_library/evaluation-manifest.schema.json": (
        "bbb75601859092f5d5b6438a99bb446899a6bba1dc802261d17f2c5efa4b3d36"
    ),
}
PUBLIC_DATA_FILES = set(PUBLIC_DATA_SHA256)

PUBLIC_EVIDENCE_SHA256 = {
    "docs/evidence/README.md": "738ce4ce55236b35335671ffb540d6a8fb5fc92ca5fb715a1ccb8ef22a6c0cf1",
    "docs/evidence/media-smoke-report.html": "d9376b099035655a0df7fe5fb8785ff2032a25271a3449d78dd890783cc0f492",
    "docs/evidence/media-smoke-report.json": "c8df6e8d18554ad40caf97ef7cb598d3e875f8c416bcd653a3e72b7d28ab00e0",
    "docs/evidence/ui-completed-evidence.png": "81a06d88625e8ca261f8750d75ee418014a2ebb27a22d0df3aea94b784504199",
    "docs/evidence/ui-desktop-initial.png": "87b514076308c02700736568ec8f835dffc27d79ddf5aa0bcdf5fa6c231cedaf",
    "docs/evidence/ui-desktop-workspace.png": "edc6a1838b3991a6733512c784260af05ff7425f3a24ef5f0846008006f7aaa3",
    "docs/evidence/ui-mobile-completed-evidence.png": "fcbb21207519b0a9626b99f211c758a9f3f4199e4ef9563925980aff328532bc",
    "docs/evidence/ui-mobile-iab-390.png": "86e2ca3dc3e7c2a4bc4316c829c1279e857ac210f1efa94abf3a57188f43f47f",
    "docs/evidence/ui-mobile-media-controls.png": "e9cb3cf14f8fb809a907b28f952815265f8e7304a57650746e48cab89a509e2b",
    "docs/evidence/ui-mobile-media-entry.png": "62bca4a10d3e91e59f0a8d06d7a860f06e72529e69486d460369855e56a198c4",
}
PUBLIC_EVIDENCE_FILES = set(PUBLIC_EVIDENCE_SHA256)

PUBLIC_DEMO_MEDIA_SHA256 = {
    "demo_media/comparison.png": "bc898c845da702417b5942e33d66ebd566cb8d068916c4a2ad56d0a3814bcf4e",
    "demo_media/frames/frame_01.png": "9c0de1026b0e73f765975bf9b12218b16b365e6339ce6295c2a290e6770e1992",
    "demo_media/frames/frame_02.png": "1fdacee712cb4a2c429cb7e8742f92ffdf4d4d45f17ccfd8c2a2baf9a2f123db",
    "demo_media/frames/frame_03.png": "16f56ec9aa606611ba3575d290213ae4d22e84e65abb2d1ebecc266896cbf1ff",
    "demo_media/frames/frame_04.png": "e1ebe2db91b42363606769915b21abbaff8c620be2cc09d95a9976998fd04466",
    "demo_media/frames/frame_05.png": "8df91ce7f5005d2f15e2b0ca2c3ec737ceccb8c82975b380c45bb14df730804c",
    "demo_media/frames/frame_06.png": "ed6d914a5a1b81be0b7a60aeb1df4b26155fc61d97d83b2bfc8ededfce86f841",
    "demo_media/reference.png": "4d66cbebd83cef1a43d37d169497163abf8eddd32824ff90d05885fd2d3cd28c",
    "demo_media/synthetic_orbit.mp4": "95da420496fcd9508ab58b54fa58df8002908a7429f91361a84f2d26506f6a2f",
}
PUBLIC_DEMO_CONTROL_SHA256 = {
    "demo_media/README.md": "54224212cc05a20acb38fad93ebbccbdcc321001d2e86f2cb0eae69ee2972ca6",
    "demo_media/manifest.json": "e2ee78ff656deea3356c2c28b60c25473f6503119d50848479bc6f0de6d86552",
    "demo_media/SHA256SUMS": "4ef5ee2d6d10cdc83f1bc8435da84c472925b334c619bf5b60f6b6af9b2830e9",
}
PUBLIC_DEMO_FILES = set(PUBLIC_DEMO_MEDIA_SHA256) | set(
    PUBLIC_DEMO_CONTROL_SHA256
)

PUBLIC_ENV_EXAMPLES = {
    ".env.example",
    ".env.v2.example",
    ".env.v2.lab.example",
    ".env.v2.nim.example",
}

PINNED_PUBLIC_SHA256 = {
    **PUBLIC_DATA_SHA256,
    **PUBLIC_EVIDENCE_SHA256,
    **PUBLIC_DEMO_MEDIA_SHA256,
    **PUBLIC_DEMO_CONTROL_SHA256,
}

FORBIDDEN_PREFIXES = (
    "runtime/",
    "secrets/",
    "work/",
    "cache/",
    "caches/",
    "hf-cache/",
    "vllm-cache/",
    "uploads/",
)

FORBIDDEN_DIRECTORY_NAMES = {
    "runtime",
    "secrets",
    "uploads",
    "customer-uploads",
    "customer-data",
    "private-artworks",
    "private-data",
    "test-pack",
    "test-packs",
}

PRIVATE_CONFIG_NAMES = {".envrc", ".netrc", ".npmrc", ".pypirc"}

MEDIA_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".avif",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".bmp",
    ".gif",
    ".dng",
    ".raw",
    ".cr2",
    ".cr3",
    ".nef",
    ".arw",
    ".raf",
    ".orf",
    ".rw2",
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
    ".avi",
    ".mkv",
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".flac",
}

CONTROLLED_SUFFIXES = {
    ".csv",
    ".tsv",
    ".parquet",
    ".arrow",
    ".feather",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".npz",
    ".npy",
    ".pt",
    ".pth",
    ".safetensors",
    ".pkl",
    ".pickle",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".zip",
    ".7z",
    ".tar",
    ".tgz",
    ".gz",
    ".bz2",
    ".xz",
    ".rar",
    ".zst",
}

MAX_PUBLIC_FILE_BYTES = 10 * 1024 * 1024

REQUIRED_GITIGNORE_RULES = {
    ".env",
    ".env.*",
    "!.env.v2.nim.example",
    "secrets/",
    "runtime/",
    "work/",
    "data/*",
    "!data/knowledge_manifest.json",
    "!data/reference_library/",
    "data/reference_library/*",
    "!data/reference_library/README.md",
    "!data/reference_library/manifest.schema.json",
    "!data/reference_library/evaluation-manifest.schema.json",
}

ALLOWED_GIT_NEGATIONS = {
    "!.env.example",
    "!.env.v2.example",
    "!.env.v2.lab.example",
    "!.env.v2.nim.example",
    "!data/knowledge_manifest.json",
    "!data/reference_library/",
    "!data/reference_library/README.md",
    "!data/reference_library/manifest.schema.json",
    "!data/reference_library/evaluation-manifest.schema.json",
}

REQUIRED_DOCKER_BASE_RULES = {
    "*",
    "!data/",
    "!data/knowledge_manifest.json",
    "!data/reference_library/",
    "!data/reference_library/README.md",
    "!data/reference_library/manifest.schema.json",
    "!data/reference_library/evaluation-manifest.schema.json",
}

REQUIRED_DOCKER_FINAL_DENY_RULES = {
    "**/.env",
    "**/.env.*",
    "**/secrets",
    "**/secrets/**",
    "**/runtime",
    "**/runtime/**",
    "**/uploads",
    "**/uploads/**",
    "**/customer-uploads",
    "**/customer-uploads/**",
    "**/customer-data",
    "**/customer-data/**",
    "**/private-artworks",
    "**/private-artworks/**",
    "**/private-data",
    "**/private-data/**",
    "**/test-pack",
    "**/test-pack/**",
    "**/test-packs",
    "**/test-packs/**",
    "**/*.pem",
    "**/*.key",
    "**/*.p12",
    "**/*.pfx",
    "**/*.jks",
    "**/*.keystore",
    "**/*.sqlite",
    "**/*.sqlite3",
    "**/*.db",
    "**/*.csv",
    "**/*.tsv",
    "**/*.parquet",
    "**/*.arrow",
    "**/*.feather",
    "**/*.npz",
    "**/*.npy",
    "**/*.pt",
    "**/*.pth",
    "**/*.safetensors",
    "**/*.pkl",
    "**/*.pickle",
    "**/*.zip",
    "**/*.7z",
    "**/*.tar",
    "**/*.tgz",
    "**/*.gz",
    "**/*.bz2",
    "**/*.xz",
    "**/*.rar",
    "**/*.zst",
    "**/*.jpg",
    "**/*.jpeg",
    "**/*.png",
    "**/*.webp",
    "**/*.avif",
    "**/*.tif",
    "**/*.tiff",
    "**/*.heic",
    "**/*.heif",
    "**/*.bmp",
    "**/*.gif",
    "**/*.dng",
    "**/*.raw",
    "**/*.cr2",
    "**/*.cr3",
    "**/*.nef",
    "**/*.arw",
    "**/*.raf",
    "**/*.orf",
    "**/*.rw2",
    "**/*.mp4",
    "**/*.mov",
    "**/*.m4v",
    "**/*.webm",
    "**/*.avi",
    "**/*.mkv",
    "**/*.wav",
    "**/*.mp3",
    "**/*.m4a",
    "**/*.aac",
    "**/*.flac",
}

ALLOWED_DOCKER_NEGATIONS = {
    "!Dockerfile",
    "!Dockerfile.vllm",
    "!Dockerfile.embedding",
    "!requirements.lock",
    "!requirements-embedding.lock",
    "!app/",
    "!app/**",
    "!embedding_server/",
    "!embedding_server/**",
    "!data/",
    "!data/knowledge_manifest.json",
    "!data/reference_library/",
    "!data/reference_library/README.md",
    "!data/reference_library/manifest.schema.json",
    "!data/reference_library/evaluation-manifest.schema.json",
    "!scripts/",
    "!scripts/import-reference-library.py",
    "!scripts/build-reference-vector-index.py",
    "!scripts/evaluate-reference-recognition.py",
    "!scripts/seal-reference-calibration.py",
    "!deploy/vllm-entrypoint.sh",
}


def _git(repo: Path, *args: str, check: bool = True) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or "Git command failed")
    return completed.stdout


def _safe_path(encoded: bytes) -> str:
    value = os.fsdecode(encoded)
    if "\\" in value or "\n" in value or "\r" in value:
        raise ValueError(f"unsafe repository path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise ValueError(f"unsafe repository path: {value!r}")
    return value


def _validate_repo_root(repo: Path) -> None:
    root = Path(
        _git(repo, "rev-parse", "--show-toplevel")
        .decode("utf-8", errors="strict")
        .strip()
    ).resolve()
    if root != repo.resolve():
        raise ValueError("--repo must name the Git worktree root")


def _candidate_paths(repo: Path) -> set[str]:
    raw = _git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return {_safe_path(item) for item in raw.split(b"\0") if item}


def _index_entries(repo: Path) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    raw = _git(repo, "ls-files", "-s", "-z")
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, object_id, stage = metadata.decode("ascii").split()
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Git index contains an unparsable entry") from exc
        if stage != "0":
            raise ValueError("Git index contains an unresolved merge entry")
        entries[_safe_path(encoded_path)] = (mode, object_id)
    return entries


def _staged_paths(repo: Path) -> set[str]:
    raw = _git(repo, "diff", "--cached", "--name-only", "-z")
    return {_safe_path(item) for item in raw.split(b"\0") if item}


def _history_entries(
    repo: Path,
) -> tuple[dict[str, set[tuple[str, str]]], list[str]]:
    issues: list[str] = []
    shallow = (
        _git(repo, "rev-parse", "--is-shallow-repository")
        .decode("ascii", errors="strict")
        .strip()
    )
    if shallow == "true":
        issues.append(
            "Git history is shallow; fetch complete history before public-release audit"
        )
        return {}, issues

    commits = _git(repo, "rev-list", "--all").decode("ascii").split()
    entries: dict[str, set[tuple[str, str]]] = {}
    for commit in commits:
        raw = _git(repo, "ls-tree", "-rz", "--full-tree", commit)
        for record in raw.split(b"\0"):
            if not record:
                continue
            try:
                metadata, encoded_path = record.split(b"\t", 1)
                mode, object_type, object_id = metadata.decode("ascii").split()
            except (ValueError, UnicodeError) as exc:
                raise ValueError("Git history contains an unparsable entry") from exc
            if object_type != "blob":
                continue
            path = _safe_path(encoded_path)
            entries.setdefault(path, set()).add((mode, object_id))
    return entries, issues


def _object_bytes(repo: Path, object_id: str) -> bytes:
    return _git(repo, "cat-file", "blob", object_id)


def _object_size(repo: Path, object_id: str) -> int:
    return int(
        _git(repo, "cat-file", "-s", object_id)
        .decode("ascii", errors="strict")
        .strip()
    )


def _active_rule_list(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _dockerfile_instructions(text: str) -> list[str]:
    instructions: list[str] = []
    pending = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pending = f"{pending} {stripped}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        instructions.append(pending)
        pending = ""
    if pending:
        instructions.append(pending)
    return instructions


def _audit_policy_snapshot(
    gitignore_bytes: bytes,
    dockerignore_bytes: bytes,
    dockerfile_bytes: bytes,
    *,
    label: str,
) -> list[str]:
    issues: list[str] = []
    try:
        gitignore_text = gitignore_bytes.decode("utf-8", errors="strict")
        dockerignore_text = dockerignore_bytes.decode("utf-8", errors="strict")
        dockerfile_text = dockerfile_bytes.decode("utf-8", errors="strict")
    except UnicodeError:
        return [f"{label} public-release policy files must be UTF-8 text"]

    git_rule_list = _active_rule_list(gitignore_text)
    git_rules = set(git_rule_list)
    for rule in sorted(REQUIRED_GITIGNORE_RULES - git_rules):
        issues.append(f"{label} .gitignore is missing controlled-data rule: {rule}")
    for rule in sorted(git_rules):
        if rule.startswith("!") and rule not in ALLOWED_GIT_NEGATIONS:
            issues.append(f"{label} .gitignore has an unapproved allow rule: {rule}")

    docker_rule_list = _active_rule_list(dockerignore_text)
    docker_rules = set(docker_rule_list)
    required_docker = REQUIRED_DOCKER_BASE_RULES | REQUIRED_DOCKER_FINAL_DENY_RULES
    for rule in sorted(required_docker - docker_rules):
        issues.append(f"{label} .dockerignore is missing controlled-data rule: {rule}")
    for rule in sorted(docker_rules):
        if rule.startswith("!") and rule not in ALLOWED_DOCKER_NEGATIONS:
            issues.append(
                f"{label} .dockerignore has an unapproved allow rule: {rule}"
            )

    allow_positions = [
        index for index, rule in enumerate(docker_rule_list) if rule.startswith("!")
    ]
    last_allow = max(allow_positions, default=-1)
    for rule in sorted(REQUIRED_DOCKER_FINAL_DENY_RULES):
        positions = [
            index for index, candidate in enumerate(docker_rule_list) if candidate == rule
        ]
        if positions and max(positions) <= last_allow:
            issues.append(
                f"{label} .dockerignore guard must follow every source allow rule: {rule}"
            )

    required_sources = set(PUBLIC_DATA_FILES)
    observed_sources: set[str] = set()
    for instruction in _dockerfile_instructions(dockerfile_text):
        try:
            tokens = shlex.split(instruction)
        except ValueError:
            issues.append(f"{label} Dockerfile contains an unparsable instruction")
            continue
        if not tokens or tokens[0].upper() not in {"COPY", "ADD"}:
            continue
        arguments = [token for token in tokens[1:] if not token.startswith("--")]
        sources = arguments[:-1] if len(arguments) >= 2 else []
        for source in sources:
            normalized = source.removeprefix("./").rstrip("/")
            if normalized == "data" or (
                "*" in normalized and normalized.startswith("data/")
            ):
                issues.append(
                    f"{label} Dockerfile may not copy the whole data tree or a data wildcard"
                )
            if normalized.startswith("data/"):
                observed_sources.add(normalized)
                if normalized not in PUBLIC_DATA_FILES:
                    issues.append(
                        f"{label} Dockerfile copies unapproved data input: {normalized}"
                    )
    for source in sorted(required_sources - observed_sources):
        issues.append(
            f"{label} Dockerfile does not copy required public data input: {source}"
        )
    return issues


def _audit_policy_files(
    repo: Path,
    index_entries: dict[str, tuple[str, str]],
    staged_paths: set[str],
) -> list[str]:
    issues: list[str] = []
    names = (".gitignore", ".dockerignore", "Dockerfile")
    worktree: list[bytes] = []
    for name in names:
        path = repo / name
        if path.is_symlink() or not path.is_file():
            issues.append(f"required public-release policy file is unavailable: {name}")
            continue
        worktree.append(path.read_bytes())
    if len(worktree) == len(names):
        issues.extend(
            _audit_policy_snapshot(*worktree, label="working tree")
        )

    if staged_paths.intersection(names):
        index_snapshot: list[bytes] = []
        for name in names:
            entry = index_entries.get(name)
            if entry is None or entry[0] == "120000":
                issues.append(f"staged public-release policy file is unavailable: {name}")
                continue
            index_snapshot.append(_object_bytes(repo, entry[1]))
        if len(index_snapshot) == len(names):
            issues.extend(
                _audit_policy_snapshot(*index_snapshot, label="staged index")
            )
    return issues


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _worktree_blob(repo: Path, path: str) -> bytes | None:
    source = repo / path
    if source.is_symlink() or not source.is_file():
        return None
    return source.read_bytes()


def _audit_demo_media_snapshot(
    candidates: set[str],
    read_blob: Callable[[str], bytes | None],
    *,
    label: str,
) -> tuple[set[str], list[str]]:
    issues: list[str] = []
    demo_paths = {path for path in candidates if path.startswith("demo_media/")}
    if not demo_paths:
        return set(), issues

    for required in sorted(PUBLIC_DEMO_FILES - demo_paths):
        issues.append(f"{label} synthetic demo media is missing approved file: {required}")
    for path in sorted(demo_paths - PUBLIC_DEMO_FILES):
        issues.append(f"{label} unapproved file under demo_media: {path}")

    manifest_bytes = read_blob("demo_media/manifest.json")
    if manifest_bytes is None:
        issues.append(f"{label} synthetic demo manifest is unavailable")
        return PUBLIC_DEMO_FILES, issues
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        issues.append(
            f"{label} synthetic demo manifest is unreadable: {type(exc).__name__}"
        )
        return PUBLIC_DEMO_FILES, issues
    if manifest.get("schema") != "relicscope.synthetic-media.v1":
        issues.append(f"{label} synthetic demo manifest has an unsupported schema")
    if manifest.get("provenance") != "DEMO/SYNTHETIC":
        issues.append(
            f"{label} synthetic demo manifest is missing DEMO/SYNTHETIC provenance"
        )
    if manifest.get("contains_real_artifact_media") is not False:
        issues.append(
            f"{label} synthetic demo manifest does not exclude real artifact media"
        )

    declared: dict[str, str] = {}
    entries = manifest.get("files")
    if not isinstance(entries, list):
        issues.append(f"{label} synthetic demo manifest files must be an array")
        entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            issues.append(f"{label} synthetic demo manifest has an invalid entry")
            continue
        relative = entry.get("path")
        expected = entry.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            issues.append(
                f"{label} synthetic demo manifest entry is missing path or SHA-256"
            )
            continue
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or relative != pure.as_posix()
            or not relative
        ):
            issues.append(
                f"{label} synthetic demo manifest has an unsafe path: {relative!r}"
            )
            continue
        public_path = f"demo_media/{relative}"
        if public_path in declared:
            issues.append(
                f"{label} synthetic demo manifest repeats a path: {public_path}"
            )
            continue
        if len(expected) != 64 or any(
            character not in "0123456789abcdef" for character in expected
        ):
            issues.append(
                f"{label} synthetic demo manifest has an invalid SHA-256: {public_path}"
            )
            continue
        declared[public_path] = expected

    if set(declared) != set(PUBLIC_DEMO_MEDIA_SHA256):
        issues.append(
            f"{label} synthetic demo manifest differs from the reviewed media allowlist"
        )
    for path, trusted in sorted(PUBLIC_DEMO_MEDIA_SHA256.items()):
        if declared.get(path) not in {None, trusted}:
            issues.append(
                f"{label} synthetic demo manifest hash is not approved: {path}"
            )

    checksum_bytes = read_blob("demo_media/SHA256SUMS")
    checksums: dict[str, str] = {}
    if checksum_bytes is None:
        issues.append(f"{label} synthetic demo checksum file is unavailable")
    else:
        try:
            checksum_text = checksum_bytes.decode("utf-8", errors="strict")
        except UnicodeError:
            issues.append(f"{label} synthetic demo checksum file is not UTF-8")
            checksum_text = ""
        for line_number, raw_line in enumerate(checksum_text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            fields = line.split(maxsplit=1)
            if len(fields) != 2:
                issues.append(
                    f"{label} synthetic demo checksum line {line_number} is invalid"
                )
                continue
            digest, relative = fields
            relative = relative.removeprefix("*")
            pure = PurePosixPath(relative)
            if (
                len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or pure.is_absolute()
                or ".." in pure.parts
                or relative != pure.as_posix()
                or not relative
            ):
                issues.append(
                    f"{label} synthetic demo checksum line {line_number} is invalid"
                )
                continue
            public_path = f"demo_media/{relative}"
            if public_path in checksums:
                issues.append(
                    f"{label} synthetic demo checksum repeats a path: {public_path}"
                )
                continue
            checksums[public_path] = digest
    if checksums != PUBLIC_DEMO_MEDIA_SHA256:
        issues.append(
            f"{label} synthetic demo checksum file differs from the reviewed allowlist"
        )

    for path, trusted in sorted(PUBLIC_DEMO_MEDIA_SHA256.items()):
        payload = read_blob(path)
        if payload is None:
            issues.append(f"{label} synthetic demo media is unavailable: {path}")
            continue
        if len(payload) > MAX_PUBLIC_FILE_BYTES:
            issues.append(f"{label} synthetic demo media exceeds size limit: {path}")
        if _sha256_bytes(payload) != trusted:
            issues.append(f"{label} synthetic demo media SHA-256 mismatch: {path}")
    return PUBLIC_DEMO_FILES, issues


def _path_boundary_issues(path: str, *, prefix: str = "") -> list[str]:
    issues: list[str] = []
    lower = path.lower()
    parts = PurePosixPath(lower).parts
    basename = parts[-1]
    suffix = Path(lower).suffix
    lead = f"{prefix} " if prefix else ""
    if (
        basename == ".env"
        or basename.startswith(".env.")
        or basename in PRIVATE_CONFIG_NAMES
    ) and path not in PUBLIC_ENV_EXAMPLES:
        issues.append(f"{lead}private environment file is release-visible: {path}")
    if lower.startswith(FORBIDDEN_PREFIXES) or any(
        part in FORBIDDEN_DIRECTORY_NAMES for part in parts[:-1]
    ):
        issues.append(f"{lead}private runtime path is release-visible: {path}")
    if path.startswith("data/") and path not in PUBLIC_DATA_FILES:
        issues.append(f"{lead}controlled data path is release-visible: {path}")
    if path.startswith("docs/evidence/") and path not in PUBLIC_EVIDENCE_FILES:
        issues.append(f"{lead}unapproved evidence artifact is release-visible: {path}")
    if path.startswith("demo_media/") and path not in PUBLIC_DEMO_FILES:
        issues.append(f"{lead}unapproved demo artifact is release-visible: {path}")
    if (
        suffix in MEDIA_SUFFIXES
        and path not in PUBLIC_DEMO_MEDIA_SHA256
        and path not in PUBLIC_EVIDENCE_FILES
    ):
        issues.append(f"{lead}raw media is outside the public allowlist: {path}")
    if suffix in CONTROLLED_SUFFIXES:
        issues.append(f"{lead}controlled/binary data file is release-visible: {path}")
    return issues


def _audit_candidate_paths(repo: Path, candidates: set[str]) -> list[str]:
    issues: list[str] = []
    _demo_allowlist, demo_issues = _audit_demo_media_snapshot(
        candidates,
        lambda path: _worktree_blob(repo, path),
        label="working tree",
    )
    issues.extend(demo_issues)
    for path in sorted(candidates):
        source = repo / path
        issues.extend(_path_boundary_issues(path))
        if source.is_symlink():
            issues.append(f"release-visible path may not be a symlink: {path}")
            continue
        if path in PINNED_PUBLIC_SHA256:
            if not source.is_file():
                issues.append(f"approved public file is unavailable: {path}")
            elif _sha256_bytes(source.read_bytes()) != PINNED_PUBLIC_SHA256[path]:
                issues.append(
                    f"approved public file changed without allowlist review: {path}"
                )
        if source.is_file() and source.stat().st_size > MAX_PUBLIC_FILE_BYTES:
            issues.append(f"release-visible file exceeds the 10 MiB limit: {path}")
    return issues


def _audit_staged_index(
    repo: Path,
    index_entries: dict[str, tuple[str, str]],
    staged_paths: set[str],
) -> list[str]:
    issues: list[str] = []
    for path in sorted(staged_paths):
        entry = index_entries.get(path)
        if entry is None:
            if path in PINNED_PUBLIC_SHA256 or path in {
                ".gitignore",
                ".dockerignore",
                "Dockerfile",
            }:
                issues.append(f"staged deletion removes required public file: {path}")
            continue
        mode, object_id = entry
        if mode == "120000":
            issues.append(f"staged release path may not be a symlink: {path}")
            continue
        if _object_size(repo, object_id) > MAX_PUBLIC_FILE_BYTES:
            issues.append(f"staged file exceeds the 10 MiB limit: {path}")
        expected = PINNED_PUBLIC_SHA256.get(path)
        if expected is not None:
            if _sha256_bytes(_object_bytes(repo, object_id)) != expected:
                issues.append(
                    f"staged public file changed without allowlist review: {path}"
                )

    if any(path.startswith("demo_media/") for path in staged_paths):
        index_paths = set(index_entries)

        def read_index(path: str) -> bytes | None:
            entry = index_entries.get(path)
            if entry is None or entry[0] == "120000":
                return None
            return _object_bytes(repo, entry[1])

        _allowlist, demo_issues = _audit_demo_media_snapshot(
            index_paths,
            read_index,
            label="staged index",
        )
        issues.extend(demo_issues)
    return issues


def _audit_history(
    repo: Path, entries: dict[str, set[tuple[str, str]]]
) -> list[str]:
    issues: list[str] = []
    for path, versions in sorted(entries.items()):
        issues.extend(_path_boundary_issues(path, prefix="Git history"))
        for mode, object_id in versions:
            if mode == "120000":
                issues.append(f"Git history contains a release symlink: {path}")
                continue
            if _object_size(repo, object_id) > MAX_PUBLIC_FILE_BYTES:
                issues.append(f"Git history contains an oversized file: {path}")
            expected = PINNED_PUBLIC_SHA256.get(path)
            if expected is not None:
                if _sha256_bytes(_object_bytes(repo, object_id)) != expected:
                    issues.append(
                        f"Git history contains an unreviewed public-file version: {path}"
                    )
    return issues


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=PROJECT_ROOT,
        help="Git worktree root to audit (default: repository containing this script)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = args.repo.expanduser().resolve()
    try:
        _validate_repo_root(repo)
        candidates = _candidate_paths(repo)
        index_entries = _index_entries(repo)
        staged_paths = _staged_paths(repo)
        history_entries, history_setup_issues = _history_entries(repo)
        issues = list(history_setup_issues)
        issues.extend(_audit_policy_files(repo, index_entries, staged_paths))
        issues.extend(_audit_candidate_paths(repo, candidates))
        issues.extend(_audit_staged_index(repo, index_entries, staged_paths))
        issues.extend(_audit_history(repo, history_entries))
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        print(f"FAIL: public release audit could not run: {exc}", file=sys.stderr)
        return 2
    if issues:
        print("FAIL: public release data boundary rejected the repository:", file=sys.stderr)
        for issue in sorted(set(issues)):
            print(f"  - {issue}", file=sys.stderr)
        return 1
    print(
        "PASS: public release boundary checked "
        f"{len(candidates)} visible files and {len(history_entries)} historical paths."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
