# Copyright (c) 2022-2026, The Isaac Lab Project Developers (https://github.com/isaac-sim/IsaacLab/blob/main/CONTRIBUTORS.md).
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Convert a JUnit XML report into the omni-github test-result artifact format."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET


_MANIFEST_NAME = "omni-github-test-results-upload.json"
_RESULT_PATH = "_testoutput/test_results.json"


def _local_name(tag: str) -> str:
    """Return an XML tag name without its namespace."""
    return tag.rsplit("}", maxsplit=1)[-1]


def _iter_testcases(root: ET.Element) -> list[ET.Element]:
    """Return all JUnit testcase elements from the parsed XML tree."""
    return [element for element in root.iter() if _local_name(element.tag) == "testcase"]


def _has_child(testcase: ET.Element, names: set[str]) -> bool:
    """Return whether a testcase has a direct child with one of the given names."""
    return any(_local_name(child.tag) in names for child in testcase)


def _duration_seconds(testcase: ET.Element) -> float:
    """Return the testcase duration in seconds."""
    try:
        duration = float(testcase.attrib.get("time", "0"))
    except ValueError:
        duration = 0.0
    return max(duration, 0.0)


def _test_id(testcase: ET.Element) -> str:
    """Return a stable test identifier from JUnit classname and name fields."""
    name = testcase.attrib.get("name", "").strip()
    classname = testcase.attrib.get("classname", "").strip()
    if classname and name:
        return f"{classname}::{name}"
    return name or classname or "unknown-testcase"


def _convert_testcase(testcase: ET.Element, test_type: str) -> dict[str, object]:
    """Convert one JUnit testcase element into an omni-github test row."""
    return {
        "test_id": _test_id(testcase),
        "passed": not _has_child(testcase, {"error", "failure"}),
        "duration": _duration_seconds(testcase),
        "test_type": test_type,
    }


def convert_junit(
    junit_file: Path,
    output_dir: Path,
    test_tool_id: str,
    test_type: str,
    app_platform: str,
    app_config: str,
) -> None:
    """Convert a JUnit XML report and write the omni-github artifact directory.

    Args:
        junit_file: Path to the source JUnit XML report.
        output_dir: Directory where the artifact root should be written.
        test_tool_id: Identifier for the test tool that produced the report.
        test_type: Test type to store on each converted test row.
        app_platform: Platform label for the result app metadata.
        app_config: Configuration label for the result app metadata.
    """
    root = ET.parse(junit_file).getroot()
    tests = [_convert_testcase(testcase, test_type) for testcase in _iter_testcases(root)]

    result: dict[str, object] = {
        "result_schema_version": 1,
        "test_tool_id": test_tool_id,
        "app": {
            "platform": app_platform,
            "config": app_config,
        },
        "tests": tests,
    }
    manifest = {
        "schema_version": 1,
        "result_paths": [_RESULT_PATH],
    }

    result_path = output_dir / _RESULT_PATH
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / _MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit-file", type=Path, required=True, help="Path to the JUnit XML report.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Artifact root directory to write.")
    parser.add_argument("--test-tool-id", required=True, help="Identifier for the test tool.")
    parser.add_argument("--test-type", required=True, help="Test type for each test row.")
    parser.add_argument("--app-platform", required=True, help="App platform label.")
    parser.add_argument("--app-config", required=True, help="App configuration label.")
    return parser.parse_args()


def main() -> None:
    """Run the converter."""
    args = parse_args()
    convert_junit(
        junit_file=args.junit_file,
        output_dir=args.output_dir,
        test_tool_id=args.test_tool_id,
        test_type=args.test_type,
        app_platform=args.app_platform,
        app_config=args.app_config,
    )


if __name__ == "__main__":
    main()
