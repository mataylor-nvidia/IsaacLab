# Copyright (c) 2022-2026, The Isaac Lab Project Developers (https://github.com/isaac-sim/IsaacLab/blob/main/CONTRIBUTORS.md).
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Tests for the omni-github JUnit result converter."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

_MODULE_PATH = Path(__file__).with_name("junit_to_omni_github_results.py")
_RESULT_PATH = "_testoutput/test_results.json"


def _load_converter_module() -> ModuleType:
    """Load the converter module from the local GitHub action directory."""
    spec = importlib.util.spec_from_file_location("junit_to_omni_github_results", _MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_git(repo_root: Path, *args: str) -> None:
    """Run a git command in a test repository."""
    subprocess.run(["git", *args], cwd=repo_root, check=True, capture_output=True, text=True)


def _init_repo(repo_root: Path) -> None:
    """Create a git repository with a committed test file."""
    test_file = repo_root / "source" / "isaaclab" / "test" / "foo" / "test_sample.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "\n".join(
            [
                "def helper():",
                "    return 1",
                "",
                "def test_fails():",
                "    assert False",
                "",
                "def test_skips():",
                "    pass",
                "",
                "def test_passes():",
                "    pass",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _run_git(repo_root, "init")
    _run_git(repo_root, "config", "user.name", "Test Owner")
    _run_git(repo_root, "config", "user.email", "owner@example.com")
    _run_git(repo_root, "add", ".")
    _run_git(repo_root, "commit", "-m", "Add tests")


def _load_rows(output_dir: Path) -> list[dict[str, object]]:
    """Load converted rows from the omni-github result artifact."""
    result = json.loads((output_dir / _RESULT_PATH).read_text(encoding="utf-8"))
    return result["tests"]


def test_convert_junit_populates_github_metadata_and_failure_details(tmp_path: Path) -> None:
    """Converted JUnit rows should carry grouping, retry, owner, and message metadata."""
    converter = _load_converter_module()
    _init_repo(tmp_path)
    junit_file = tmp_path / "report.xml"
    output_dir = tmp_path / "out"
    junit_file.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="3" failures="1" errors="0" skipped="1" time="2.0">
  <testcase classname="source.isaaclab.test.foo.test_sample" name="test_fails[param]" time="1.25">
    <failure message="AssertionError: expected 1, got 2">Traceback details</failure>
  </testcase>
  <testcase classname="source.isaaclab.test.foo.test_sample" name="test_skips" time="0.10">
    <skipped message="requires GPU"/>
  </testcase>
  <testcase classname="source.isaaclab.test.foo.test_sample" name="test_passes" time="0.65"/>
</testsuite>
""",
        encoding="utf-8",
    )

    converter.convert_junit(
        junit_file=junit_file,
        output_dir=output_dir,
        test_tool_id="pytest",
        test_type="pytest",
        app_platform="linux-x86_64",
        app_config="test-job",
        group_name="Docker + Tests / isaaclab_tasks [1/3]",
        retries=2,
        repo_root=tmp_path,
    )

    rows = _load_rows(output_dir)
    failed, skipped, passed = rows
    assert failed["test_id"] == "source.isaaclab.test.foo.test_sample::test_fails[param]"
    assert failed["test_name"] == "test_fails[param]"
    assert failed["passed"] is False
    assert failed["duration"] == 1.25
    assert failed["group_id"] == "Docker + Tests / isaaclab_tasks [1/3]"
    assert failed["retries"] == 2
    assert failed["owner"] == "owner@example.com"
    assert failed["message"] == "AssertionError: expected 1, got 2"

    assert skipped["passed"] is False
    assert skipped["skipped"] is True
    assert skipped["skip_reason"] == "requires GPU"
    assert skipped["message"] == "requires GPU"
    assert skipped["owner"] == "owner@example.com"

    assert passed["passed"] is True
    assert "message" not in passed
    assert passed["owner"] == "owner@example.com"


def test_convert_junit_marks_crashes_timeouts_and_empty_reports(tmp_path: Path) -> None:
    """Converted rows should surface crash, timeout, and empty-report messages."""
    converter = _load_converter_module()
    junit_file = tmp_path / "report.xml"
    output_dir = tmp_path / "out"
    junit_file.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="crash_suite" tests="1" failures="0" errors="1" skipped="0" time="0">
  <testcase classname="test_rendering_cartpole" name="test_execution" time="0">
    <error message="Process killed by signal 15 after timeout">diagnostics</error>
  </testcase>
</testsuite>
""",
        encoding="utf-8",
    )

    converter.convert_junit(
        junit_file=junit_file,
        output_dir=output_dir,
        test_tool_id="pytest",
        test_type="rendering-correctness",
        app_platform="linux-x86_64",
        app_config="test-job",
        group_name="Docker + Tests / environments",
        retries=0,
        repo_root=tmp_path,
    )

    rows = _load_rows(output_dir)
    assert rows == [
        {
            "crash": True,
            "duration": 0.0,
            "group_id": "Docker + Tests / environments",
            "message": "Process killed by signal 15 after timeout",
            "passed": False,
            "retries": 0,
            "test_id": "test_rendering_cartpole::test_execution",
            "test_name": "test_execution",
            "test_type": "rendering-correctness",
            "timeout": True,
        }
    ]

    empty_file = tmp_path / "empty.xml"
    empty_output_dir = tmp_path / "empty-out"
    empty_file.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="empty" tests="0" failures="0" errors="0" skipped="0" time="3.5"/>
""",
        encoding="utf-8",
    )

    converter.convert_junit(
        junit_file=empty_file,
        output_dir=empty_output_dir,
        test_tool_id="pytest",
        test_type="pytest",
        app_platform="linux-x86_64",
        app_config="test-job",
        group_name="Installation Tests / Installation Tests (x86)",
        retries=1,
        repo_root=tmp_path,
    )

    rows = _load_rows(empty_output_dir)
    assert rows == [
        {
            "duration": 3.5,
            "group_id": "Installation Tests / Installation Tests (x86)",
            "message": "JUnit report contained no testcases.",
            "passed": False,
            "retries": 1,
            "test_id": "empty::junit_report_no_testcases",
            "test_type": "pytest",
        }
    ]
