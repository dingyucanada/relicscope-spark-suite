from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _wrapper_project(tmp_path: Path, profile: str) -> Path:
    project = tmp_path / "project"
    (project / "deploy").mkdir(parents=True)
    (project / "scripts").mkdir()
    shutil.copy2(
        PROJECT_ROOT / "deploy/single-spark-accept.sh",
        project / "deploy/single-spark-accept.sh",
    )
    (project / ".env").write_text(
        f"MODEL_PROFILE={profile}\nVISION_MODEL=test_qwen_model\n",
        encoding="utf-8",
    )
    return project


def test_formal_wrapper_rejects_nemotron_before_docker_is_called(tmp_path):
    project = _wrapper_project(tmp_path, "nemotron-omni")
    completed = subprocess.run(
        ["bash", "deploy/single-spark-accept.sh"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "Qwen3-VL baseline-only" in completed.stderr


def test_qwen_wrapper_passes_exact_profile_model_container_and_output(tmp_path):
    project = _wrapper_project(tmp_path, "qwen3-vl")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "python-args.txt"
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\necho vision-container-id\n", encoding="utf-8")
    docker.chmod(0o755)
    python = fake_bin / "python3"
    python.write_text(
        """#!/bin/sh
printf '%s\n' "$@" > "$CAPTURE_FILE"
output=''
while [ "$#" -gt 0 ]; do
  if [ "$1" = '--output' ]; then output="$2"; break; fi
  shift
done
mkdir -p "$(dirname "$output")"
printf '{}\n' > "$output"
digest=$(sha256sum "$output" | awk '{print $1}')
printf '%s  %s\n' "$digest" "$(basename "$output")" > "${output}.sha256"
""",
        encoding="utf-8",
    )
    python.chmod(0o755)
    output = tmp_path / "evidence/live.json"
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["CAPTURE_FILE"] = str(capture)

    completed = subprocess.run(
        [
            "bash",
            "deploy/single-spark-accept.sh",
            "--output",
            str(output),
        ],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    arguments = capture.read_text(encoding="utf-8").splitlines()
    assert arguments[0].endswith("scripts/spark-live-acceptance.py")
    assert arguments[1] == "baseline"
    assert arguments[arguments.index("--profile") + 1] == "qwen3-vl"
    assert arguments[arguments.index("--expected-model") + 1] == "test_qwen_model"
    assert arguments[arguments.index("--vision-container-id") + 1] == (
        "vision-container-id"
    )
    assert arguments[arguments.index("--output") + 1] == str(output)
