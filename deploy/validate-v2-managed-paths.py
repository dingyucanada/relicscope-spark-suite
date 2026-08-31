#!/usr/bin/env python3
"""Fail closed when V2 host bind paths overlap or traverse symbolic links."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _existing_symlink_component(path: Path) -> Path | None:
    absolute = Path(os.path.abspath(path))
    for candidate in (absolute, *absolute.parents):
        if candidate.is_symlink():
            return candidate
    return None


def validate(project_root: Path, raw_paths: list[Path]) -> None:
    if len(raw_paths) < 2:
        raise ValueError("at least two managed paths are required")
    project = os.path.realpath(project_root)
    home = os.path.realpath(Path.home())
    resolved: list[str] = []
    for raw_path in raw_paths:
        if not raw_path.is_absolute():
            raise ValueError(f"managed path must be absolute: {raw_path}")
        symlink = _existing_symlink_component(raw_path)
        if symlink is not None:
            raise ValueError(f"managed path must not traverse a symlink: {symlink}")
        real_path = os.path.realpath(raw_path)
        forbidden = {
            "/",
            "/home",
            "/mnt",
            "/opt",
            "/srv",
            "/tmp",
            "/usr",
            "/var",
            home,
            project,
        }
        if real_path in forbidden:
            raise ValueError(f"refusing broad managed path: {real_path}")
        resolved.append(real_path)

    for index, left in enumerate(resolved):
        for right in resolved[index + 1 :]:
            if left == right or os.path.commonpath((left, right)) in {left, right}:
                raise ValueError(
                    f"managed paths must be separate and non-nested: {left} ; {right}"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        validate(args.project_root, args.paths)
    except ValueError as exc:
        parser.exit(2, f"FAIL: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
