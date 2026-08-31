from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "relicscope_v2_env_reader", PROJECT_ROOT / "deploy" / "read-v2-env.py"
)
assert SPEC is not None and SPEC.loader is not None
ENV_READER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ENV_READER)


def test_reads_single_double_and_unquoted_crlf_values(tmp_path):
    env_file = tmp_path / ".env.v2"
    env_file.write_bytes(
        b"SCOUT_HOSTNAME='scout.spark.local'\r\n"
        b'CADDY_DATA_DIR="./runtime/caddy data"\r\n'
        b"SCOUT_HTTPS_PORT=8443\r\n"
    )
    assert ENV_READER.read_value(env_file, "SCOUT_HOSTNAME") == "scout.spark.local"
    assert ENV_READER.read_value(env_file, "CADDY_DATA_DIR") == (
        "./runtime/caddy data"
    )
    assert ENV_READER.read_value(env_file, "SCOUT_HTTPS_PORT") == "8443"


def test_last_assignment_wins_and_empty_uses_default(tmp_path):
    env_file = tmp_path / ".env.v2"
    env_file.write_text(
        "RELICSCOPE_DATA_HOST_DIR=./first\n"
        "export RELICSCOPE_DATA_HOST_DIR='./second path'\n"
        "CADDY_DATA_DIR=\n",
        encoding="utf-8",
    )
    assert ENV_READER.read_value(env_file, "RELICSCOPE_DATA_HOST_DIR") == (
        "./second path"
    )
    assert ENV_READER.read_value(env_file, "CADDY_DATA_DIR", "fallback") == (
        "fallback"
    )


def test_shell_syntax_is_returned_as_data_and_never_evaluated(tmp_path):
    env_file = tmp_path / ".env.v2"
    marker = tmp_path / "must-not-exist"
    env_file.write_text(
        f'CADDY_DATA_DIR="$(touch {marker})"\n',
        encoding="utf-8",
    )
    assert ENV_READER.read_value(env_file, "CADDY_DATA_DIR") == (
        f"$(touch {marker})"
    )
    assert not marker.exists()


def test_rejects_malformed_quote_and_oversized_file(tmp_path):
    env_file = tmp_path / ".env.v2"
    env_file.write_text('CADDY_DATA_DIR="unterminated\n', encoding="utf-8")
    with pytest.raises(ValueError, match="unterminated"):
        ENV_READER.read_value(env_file, "CADDY_DATA_DIR")

    env_file.write_bytes(b"X=" + b"a" * (ENV_READER.MAX_ENV_BYTES + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        ENV_READER.read_value(env_file, "X")
