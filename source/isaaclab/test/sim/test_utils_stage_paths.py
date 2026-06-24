# Copyright (c) 2022-2026, The Isaac Lab Project Developers (https://github.com/isaac-sim/IsaacLab/blob/main/CONTRIBUTORS.md).
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Unit tests for stage path helpers (no Kit required)."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import pytest

from isaaclab.sim.utils.stage import _is_uri_path


@pytest.mark.parametrize(
    ("asset_path", "expected"),
    [
        pytest.param("https://example.com/asset.png", True, id="https_uri"),
        pytest.param("omniverse://server/Library/asset.png", True, id="omniverse_uri"),
        pytest.param("s3://bucket/Library/asset.png", True, id="s3_uri"),
        pytest.param("custom+resolver.scheme://Library/asset.png", True, id="custom_scheme_uri"),
        pytest.param("file://C:/assets/texture.png", True, id="file_uri"),
        pytest.param("", False, id="empty"),
        pytest.param("relative/asset.png", False, id="relative_path"),
        pytest.param("/absolute/asset.png", False, id="absolute_posix_path"),
        pytest.param("C:/assets/texture.png", False, id="windows_drive_path"),
        pytest.param("C://assets/texture.png", False, id="windows_drive_path_double_slash"),
        pytest.param(r"C:\assets\texture.png", False, id="windows_drive_path_backslashes"),
        pytest.param("://missing-scheme/asset.png", False, id="missing_scheme"),
        pytest.param("1scheme://asset.png", False, id="scheme_starts_with_digit"),
        pytest.param("bad_scheme://asset.png", False, id="scheme_contains_underscore"),
        pytest.param("bad scheme://asset.png", False, id="scheme_contains_space"),
        pytest.param("https:/asset.png", False, id="missing_uri_separator"),
    ],
)
def test_is_uri_path_identifies_uri_schemes(asset_path: str, expected: bool):
    """Test URI scheme detection without resolving or touching the filesystem."""
    assert _is_uri_path(asset_path) is expected
