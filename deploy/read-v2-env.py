#!/usr/bin/env python3
"""Read one .env value without evaluating shell syntax."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ASSIGNMENT_PATTERN = re.compile(
    r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)$"
)
MAX_ENV_BYTES = 1024 * 1024


def read_value(path: Path, key: str, default: str = "") -> str:
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError("environment key is invalid")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return default
    if len(raw) > MAX_ENV_BYTES:
        raise ValueError("environment file exceeds the 1 MiB safety limit")
    if b"\x00" in raw:
        raise ValueError("environment file contains a NUL byte")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("environment file is not valid UTF-8") from exc

    selected: str | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ASSIGNMENT_PATTERN.fullmatch(line)
        if match is None:
            continue
        name, raw_value = match.groups()
        if name != key:
            continue
        value = raw_value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise ValueError(
                    f"unterminated quoted value for {key} on line {line_number}"
                )
            value = value[1:-1]
        elif value.endswith(("'", '"')):
            raise ValueError(f"unmatched quote for {key} on line {line_number}")
        if any(character in value for character in ("\x00", "\r", "\n")):
            raise ValueError(f"unsafe control character in {key}")
        selected = value
    return selected if selected else default


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--key", required=True)
    parser.add_argument("--default", default="")
    args = parser.parse_args()
    try:
        value = read_value(args.file, args.key, args.default)
    except ValueError as exc:
        parser.exit(2, f"FAIL: {exc}\n")
    print(value, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
