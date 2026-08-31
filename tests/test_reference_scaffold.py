from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "scaffold-reference-library.py"


def _module():
    spec = importlib.util.spec_from_file_location("reference_scaffold", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scaffold_creates_blank_50_plus_10_intake_workspace(tmp_path: Path) -> None:
    output = tmp_path / "intake"
    result = _module().scaffold(output)

    assert result["reference_artifacts"] == 50
    assert result["counterfeit_records"] == 10
    assert result["planned_images"] == 300
    with (output / "object-intake.csv").open(encoding="utf-8", newline="") as stream:
        objects = list(csv.DictReader(stream))
    with (output / "image-intake.csv").open(encoding="utf-8", newline="") as stream:
        images = list(csv.DictReader(stream))
    assert len(objects) == 60
    assert len(images) == 300
    assert {row["angle"] for row in images if row["artifact_id"] == "REF-001"} == {
        "FRONT",
        "BACK",
        "LEFT_PROFILE",
        "RIGHT_PROFILE",
        "BASE",
    }
    assert all(not row["sha256"] for row in images)


def test_scaffold_refuses_nonempty_or_below_policy_directory(tmp_path: Path) -> None:
    module = _module()
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "owned.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="must be empty"):
        module.scaffold(occupied)
    with pytest.raises(ValueError, match="at least 50"):
        module.scaffold(tmp_path / "small", reference_count=49)
