# Copyright (c) 2022-2026, The Isaac Lab Project Developers (https://github.com/isaac-sim/IsaacLab/blob/main/CONTRIBUTORS.md).
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Convert a JUnit XML report into the omni-github test-result artifact format."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

_MANIFEST_NAME = "omni-github-test-results-upload.json"
_RESULT_PATH = "_testoutput/test_results.json"
_EMPTY_REPORT_TEST_NAME = "junit_report_no_testcases"
_MAX_MESSAGE_CHARS = 500
_SKIP_INDEX_DIRS = {
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "_isaac_sim",
    "logs",
    "tmp",
}
_CRASH_PATTERNS = (
    "core dumped",
    "crash",
    "segmentation fault",
    "signal ",
    "process killed",
)
_TIMEOUT_PATTERNS = (
    "deadline exceeded",
    "timed out",
    "timeout",
)


class _SourceIndex:
    """Resolve JUnit testcases to local Python source files and line numbers."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self._module_to_path: dict[str, Path] = {}
        self._basename_to_paths: dict[str, list[Path]] = {}
        self._line_cache: dict[tuple[Path, str], int | None] = {}
        self._build()

    def resolve_test_line(self, testcase: ET.Element) -> tuple[Path, int] | None:
        """Return the source file and 1-based line for a JUnit testcase."""
        path = self._resolve_path(testcase)
        if path is None:
            return None

        line = self._line_from_attribute(testcase)
        if line is None:
            line = self._resolve_line_from_ast(path, testcase.attrib.get("name", ""))
        if line is None:
            return None
        return path, line

    def _build(self) -> None:
        """Build a lightweight Python file index for testcase lookup."""
        for root, dirnames, filenames in os.walk(self.repo_root):
            dirnames[:] = [dirname for dirname in dirnames if dirname not in _SKIP_INDEX_DIRS]
            root_path = Path(root)
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                path = root_path / filename
                try:
                    rel_path = path.relative_to(self.repo_root)
                except ValueError:
                    continue
                module = ".".join(rel_path.with_suffix("").parts)
                self._module_to_path[module] = path
                self._module_to_path.setdefault(module.removeprefix("source."), path)
                self._basename_to_paths.setdefault(path.stem, []).append(path)

    def _resolve_path(self, testcase: ET.Element) -> Path | None:
        """Resolve a JUnit testcase to a local source path."""
        for attr_name in ("file", "path"):
            if source_attr := testcase.attrib.get(attr_name):
                path = (self.repo_root / source_attr).resolve()
                if path.is_file():
                    return path

        classname = testcase.attrib.get("classname", "").strip()
        if not classname:
            return None

        pathish = classname.replace("\\", "/")
        if "/" in pathish or pathish.endswith(".py"):
            path = (self.repo_root / pathish).with_suffix(".py").resolve()
            if path.is_file():
                return path

        module_path = self._module_to_path.get(classname) or self._module_to_path.get(classname.removeprefix("source."))
        if module_path is not None:
            return module_path

        candidates = self._basename_to_paths.get(classname.rsplit(".", maxsplit=1)[-1], [])
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            test_name = testcase.attrib.get("name", "")
            containing_name = [path for path in candidates if self._resolve_line_from_ast(path, test_name) is not None]
            if len(containing_name) == 1:
                return containing_name[0]
        return None

    def _line_from_attribute(self, testcase: ET.Element) -> int | None:
        """Return a 1-based source line from common JUnit attributes."""
        for attr_name in ("line", "lineno"):
            value = testcase.attrib.get(attr_name)
            if value is None:
                continue
            try:
                line = int(value)
            except ValueError:
                continue
            if line >= 0:
                return line + 1 if line == 0 else line
        return None

    def _resolve_line_from_ast(self, path: Path, test_name: str) -> int | None:
        """Resolve a pytest test name to its definition line."""
        cache_key = (path, test_name)
        if cache_key in self._line_cache:
            return self._line_cache[cache_key]

        target = _strip_pytest_parameters(test_name)
        target_class = None
        target_function = target
        if "." in target:
            target_class, target_function = target.split(".", maxsplit=1)

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            self._line_cache[cache_key] = None
            return None

        first_test_line: int | None = None
        resolved_line: int | None = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                first_test_line = first_test_line or node.lineno
                if target_class is None and node.name == target_function:
                    resolved_line = node.lineno
                    break
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith("Test"):
                    continue
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test_"):
                        first_test_line = first_test_line or child.lineno
                        if node.name == target_class and child.name == target_function:
                            resolved_line = child.lineno
                            break
                if resolved_line is not None:
                    break

        # Synthetic crash reports represent a whole test file as ``test_execution``.
        if resolved_line is None and target_function == "test_execution":
            resolved_line = first_test_line

        self._line_cache[cache_key] = resolved_line
        return resolved_line


class _GitBlameOwnerResolver:
    """Resolve a source line to a git author email using blame output."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self._file_owner_cache: dict[Path, dict[int, str]] = {}

    def resolve_owner(self, path: Path, line: int) -> str | None:
        """Return the git blame author email for a source line."""
        owners = self._owners_by_line(path)
        return owners.get(line)

    def _owners_by_line(self, path: Path) -> dict[int, str]:
        """Return blame owners keyed by 1-based source line for one file."""
        if path in self._file_owner_cache:
            return self._file_owner_cache[path]

        try:
            rel_path = path.resolve().relative_to(self.repo_root)
        except ValueError:
            self._file_owner_cache[path] = {}
            return {}

        try:
            completed = subprocess.run(
                ["git", "blame", "--line-porcelain", "--", rel_path.as_posix()],
                cwd=self.repo_root,
                check=False,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            self._file_owner_cache[path] = {}
            return {}

        if completed.returncode != 0:
            self._file_owner_cache[path] = {}
            return {}

        owners: dict[int, str] = {}
        current_line: int | None = None
        current_owner: str | None = None
        for output_line in completed.stdout.splitlines():
            if re.fullmatch(r"[0-9a-f]{40} \d+ \d+(?: \d+)?", output_line):
                parts = output_line.split()
                current_line = int(parts[2])
                current_owner = None
            elif output_line.startswith("author-mail "):
                current_owner = output_line[len("author-mail ") :].strip("<>")
            elif output_line.startswith("author ") and current_owner is None:
                current_owner = output_line[len("author ") :]
            elif output_line.startswith("\t") and current_line is not None:
                if current_owner:
                    owners[current_line] = current_owner
                current_line = None
                current_owner = None

        self._file_owner_cache[path] = owners
        return owners


def _local_name(tag: str) -> str:
    """Return an XML tag name without its namespace."""
    return tag.rsplit("}", maxsplit=1)[-1]


def _iter_testcases(root: ET.Element) -> list[ET.Element]:
    """Return all JUnit testcase elements from the parsed XML tree."""
    return [element for element in root.iter() if _local_name(element.tag) == "testcase"]


def _has_child(testcase: ET.Element, names: set[str]) -> bool:
    """Return whether a testcase has a direct child with one of the given names."""
    return any(_local_name(child.tag) in names for child in testcase)


def _first_child(testcase: ET.Element, names: set[str]) -> ET.Element | None:
    """Return the first direct testcase child whose local tag name matches."""
    for child in testcase:
        if _local_name(child.tag) in names:
            return child
    return None


def _duration_seconds(element: ET.Element) -> float:
    """Return the element duration in seconds."""
    try:
        duration = float(element.attrib.get("time", "0"))
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


def _strip_pytest_parameters(test_name: str) -> str:
    """Return a pytest testcase name without parameter brackets."""
    return test_name.split("[", maxsplit=1)[0]


def _short_message(element: ET.Element | None) -> str | None:
    """Return a compact message from a failure, error, or skip element."""
    if element is None:
        return None
    raw_message = element.attrib.get("message") or element.attrib.get("type") or element.text or ""
    message = " ".join(raw_message.split())
    if not message:
        return None
    if len(message) > _MAX_MESSAGE_CHARS:
        return message[: _MAX_MESSAGE_CHARS - 3].rstrip() + "..."
    return message


def _group_id(group_name: str) -> str | None:
    """Return a row group id from a label."""
    group_name = group_name.strip()
    if not group_name:
        return None
    return group_name


def _contains_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    """Return whether text contains any case-insensitive pattern."""
    normalized = text.lower()
    return any(pattern in normalized for pattern in patterns)


def _convert_testcase(
    testcase: ET.Element,
    test_type: str,
    group_id: str | None,
    retries: int,
    source_index: _SourceIndex | None,
    owner_resolver: _GitBlameOwnerResolver | None,
) -> dict[str, object]:
    """Convert one JUnit testcase element into an omni-github test row."""
    failure_or_error = _first_child(testcase, {"error", "failure"})
    skipped = _first_child(testcase, {"skipped"})
    message = _short_message(failure_or_error)
    skip_reason = _short_message(skipped)
    row: dict[str, object] = {
        "test_id": _test_id(testcase),
        "passed": failure_or_error is None and skipped is None,
        "duration": _duration_seconds(testcase),
        "test_type": test_type,
    }
    if group_id is not None:
        row["group_id"] = group_id
    row["retries"] = retries
    if testcase.attrib.get("name"):
        row["test_name"] = testcase.attrib["name"]
    if skipped is not None:
        row["skipped"] = True
    if skip_reason is not None:
        row["skip_reason"] = skip_reason
        row.setdefault("message", skip_reason)
    if message is not None:
        row["message"] = message

    detail_text = " ".join(filter(None, (message, failure_or_error.text if failure_or_error is not None else None)))
    if failure_or_error is not None and _contains_pattern(detail_text, _CRASH_PATTERNS):
        row["crash"] = True
    if failure_or_error is not None and _contains_pattern(detail_text, _TIMEOUT_PATTERNS):
        row["timeout"] = True

    if source_index is not None and owner_resolver is not None:
        source_line = source_index.resolve_test_line(testcase)
        if source_line is not None:
            source_path, line = source_line
            if owner := owner_resolver.resolve_owner(source_path, line):
                row["owner"] = owner
    return row


def _synthetic_empty_report_test(
    root: ET.Element,
    test_tool_id: str,
    test_type: str,
    group_id: str | None,
    retries: int,
) -> dict[str, object]:
    """Return a failed test row for a JUnit report that contains no testcases."""
    suite_name = root.attrib.get("name", "").strip()
    tool_name = test_tool_id.strip() or "junit"
    test_id = f"{suite_name or tool_name}::{_EMPTY_REPORT_TEST_NAME}"
    row: dict[str, object] = {
        "test_id": test_id,
        "passed": False,
        "duration": _duration_seconds(root),
        "test_type": test_type,
        "message": "JUnit report contained no testcases.",
    }
    if group_id is not None:
        row["group_id"] = group_id
    row["retries"] = retries
    return row


def convert_junit(
    junit_file: Path,
    output_dir: Path,
    test_tool_id: str,
    test_type: str,
    app_platform: str,
    app_config: str,
    group_name: str = "",
    retries: int = 0,
    repo_root: Path | None = None,
) -> None:
    """Convert a JUnit XML report and write the omni-github artifact directory.

    Args:
        junit_file: Path to the source JUnit XML report.
        output_dir: Directory where the artifact root should be written.
        test_tool_id: Identifier for the test tool that produced the report.
        test_type: Test type to store on each converted test row.
        app_platform: Platform label for the result app metadata.
        app_config: Configuration label for the result app metadata.
        group_name: Human-readable test group label to store on each test row.
        retries: Within-job retry count to store on each test row.
        repo_root: Repository root used for source lookup and git blame.
    """
    root = ET.parse(junit_file).getroot()
    row_group_id = _group_id(group_name)
    owner_repo_root = (repo_root or Path.cwd()).resolve()
    source_index = _SourceIndex(owner_repo_root) if owner_repo_root.exists() else None
    owner_resolver = _GitBlameOwnerResolver(owner_repo_root) if source_index is not None else None
    tests = [
        _convert_testcase(
            testcase,
            test_type,
            row_group_id,
            retries,
            source_index,
            owner_resolver,
        )
        for testcase in _iter_testcases(root)
    ]
    if not tests:
        print(f"::warning::No testcases found in JUnit report; uploading a synthetic failed test: {junit_file}")
        tests = [_synthetic_empty_report_test(root, test_tool_id, test_type, row_group_id, retries)]

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
    parser.add_argument("--group-name", default="", help="Human-readable group label for each test row.")
    parser.add_argument("--retries", type=int, default=0, help="Within-job retry count for each test row.")
    parser.add_argument(
        "--repo-root", type=Path, default=Path.cwd(), help="Repository root for git blame owner lookup."
    )
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
        group_name=args.group_name,
        retries=max(args.retries, 0),
        repo_root=args.repo_root,
    )


if __name__ == "__main__":
    main()
