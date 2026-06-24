# Copyright (c) 2022-2026, The Isaac Lab Project Developers (https://github.com/isaac-sim/IsaacLab/blob/main/CONTRIBUTORS.md).
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Tests for JUnit to omni-github result artifact conversion."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_ACTION_DIR = Path(__file__).resolve().parent
_MODULE_PATH = _ACTION_DIR / "junit_to_omni_github_results.py"
_SPEC = importlib.util.spec_from_file_location("junit_to_omni_github_results", _MODULE_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
junit_to_omni_github_results = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(junit_to_omni_github_results)


def test_convert_junit_writes_artifact_for_testcases(tmp_path: Path) -> None:
    """Test that converting a JUnit report writes manifest and result JSON files."""
    junit_file = tmp_path / "report.xml"
    output_dir = tmp_path / "artifact"
    junit_file.write_text(
        """
<testsuite name="sample" tests="2">
  <testcase classname="pkg.test_mod" name="test_passes" time="0.25" />
  <testcase classname="pkg.test_mod" name="test_fails" time="1.5">
    <failure message="boom">stack</failure>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )

    junit_to_omni_github_results.convert_junit(
        junit_file=junit_file,
        output_dir=output_dir,
        test_tool_id="pytest",
        test_type="rendering-correctness",
        app_platform="linux-x86_64",
        app_config="test-job",
    )

    manifest = json.loads((output_dir / "omni-github-test-results-upload.json").read_text(encoding="utf-8"))
    result = json.loads((output_dir / "_testoutput" / "test_results.json").read_text(encoding="utf-8"))

    assert manifest == {"schema_version": 1, "result_paths": ["_testoutput/test_results.json"]}
    assert result["test_tool_id"] == "pytest"
    assert result["app"] == {"platform": "linux-x86_64", "config": "test-job"}
    assert result["tests"] == [
        {
            "duration": 0.25,
            "passed": True,
            "test_id": "pkg.test_mod::test_passes",
            "test_type": "rendering-correctness",
        },
        {
            "duration": 1.5,
            "passed": False,
            "test_id": "pkg.test_mod::test_fails",
            "test_type": "rendering-correctness",
        },
    ]


def test_convert_junit_rejects_empty_test_report(tmp_path: Path) -> None:
    """Test that converting an empty JUnit report fails before writing an invalid artifact."""
    junit_file = tmp_path / "empty.xml"
    output_dir = tmp_path / "artifact"
    junit_file.write_text('<testsuite name="empty" tests="0" />', encoding="utf-8")

    with pytest.raises(ValueError, match="No testcases found"):
        junit_to_omni_github_results.convert_junit(
            junit_file=junit_file,
            output_dir=output_dir,
            test_tool_id="pytest",
            test_type="pytest",
            app_platform="linux-x86_64",
            app_config="test-job",
        )

    assert not output_dir.exists()
