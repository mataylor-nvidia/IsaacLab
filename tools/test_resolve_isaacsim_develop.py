# Copyright (c) 2022-2026, The Isaac Lab Project Developers (https://github.com/isaac-sim/IsaacLab/blob/main/CONTRIBUTORS.md).
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Unit tests for the pure parsing/selection logic of resolve_isaacsim_develop.

Only the network-free functions are covered here; ``_http_get`` and
``commit_on_branch`` touch the network and are exercised on a real runner.
"""

from __future__ import annotations

import base64

import resolve_isaacsim_develop as r

# Synthetic wheels listed on a PEP 503 simple-index page. Mixes: newest+older
# develop win builds, a develop linux build (different platform), a release-line
# win build (cp311), and a public-stable win build (no +release segment).
_WHEELS = [
    "isaacsim-6.0.0rc48+release.40557.63231095.gl-cp312-none-win_amd64.whl",
    "isaacsim-6.0.0rc47+release.40001.aaaaaaaa.gl-cp312-none-win_amd64.whl",
    "isaacsim-6.0.0rc48+release.40557.63231095.gl-cp312-none-manylinux_2_35_x86_64.whl",
    "isaacsim-5.1.0rc17+release.26116.14247817.gl-cp311-none-win_amd64.whl",
    "isaacsim-5.1.0.0-cp311-none-win_amd64.whl",
]


def _index_html(wheels: list[str]) -> str:
    """Render an anchor-per-wheel simple-index page; the first href %2B-encodes '+'."""
    rows = [f'<a href="{(w.replace("+", "%2B") if i == 0 else w)}">{w}</a><br/>' for i, w in enumerate(wheels)]
    return "<!DOCTYPE html><html><body>\n" + "\n".join(rows) + "\n</body></html>"


_INDEX_HTML = _index_html(_WHEELS)


def test_parse_extracts_version_platform_build_and_commit():
    wheels = r.parse_simple_index(_INDEX_HTML)
    # five distinct wheels, deduplicated and order-preserving
    assert len(wheels) == 5
    newest = wheels[0]
    assert newest.version == "6.0.0rc48+release.40557.63231095.gl"
    assert newest.python_tag == "cp312"
    assert newest.platform_tag == "win_amd64"
    assert newest.build == 40557
    assert newest.commit == "63231095"


def test_parse_unquotes_percent_encoded_plus_in_href():
    # the win_amd64 rc48 wheel is listed once, but its href %2B-encodes '+' while
    # its link text uses '+'; after unquoting, both collapse to one filename and
    # dedup to a single entry (the same version also exists as a separate linux wheel)
    wheels = r.parse_simple_index(_INDEX_HTML)
    win_rc48 = [
        w for w in wheels if w.version == "6.0.0rc48+release.40557.63231095.gl" and w.platform_tag == "win_amd64"
    ]
    assert len(win_rc48) == 1


def test_public_stable_build_has_no_build_or_commit():
    public = next(w for w in r.parse_simple_index(_INDEX_HTML) if w.version == "5.1.0.0")
    assert public.build is None
    assert public.commit is None


def test_select_picks_newest_build_for_platform_and_excludes_others():
    wheels = r.parse_simple_index(_INDEX_HTML)
    cands = r.select_candidates(wheels, "cp312", "win_amd64")
    # only the two develop win_amd64/cp312 builds, newest build first
    assert [w.version for w in cands] == [
        "6.0.0rc48+release.40557.63231095.gl",
        "6.0.0rc47+release.40001.aaaaaaaa.gl",
    ]


def test_select_excludes_public_builds_without_build_segment():
    wheels = r.parse_simple_index(_INDEX_HTML)
    cands = r.select_candidates(wheels, "cp311", "win_amd64")
    # the cp311 win matches are the release-line rc (kept) and public 5.1.0.0 (dropped)
    assert [w.version for w in cands] == ["5.1.0rc17+release.26116.14247817.gl"]


def test_select_version_prefix_filters_release_line():
    wheels = r.parse_simple_index(_INDEX_HTML)
    assert r.select_candidates(wheels, "cp312", "win_amd64", version_prefix="5.1.0") == []
    assert len(r.select_candidates(wheels, "cp312", "win_amd64", version_prefix="6.0.0")) == 2


def test_basic_auth_header_is_rfc7617_encoded():
    header = r._basic_auth_header("svc-user", "s3cr3t")
    scheme, _, token = header.partition(" ")
    assert scheme == "Basic"
    assert base64.b64decode(token).decode() == "svc-user:s3cr3t"
