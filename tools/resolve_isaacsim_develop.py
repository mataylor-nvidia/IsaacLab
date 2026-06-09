# Copyright (c) 2022-2026, The Isaac Lab Project Developers (https://github.com/isaac-sim/IsaacLab/blob/main/CONTRIBUTORS.md).
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Resolve the Isaac Sim wheel version aligned with the ``omni_isaac_sim`` develop branch.

The native (non-Docker) install paths -- e.g. the Windows CI -- pull Isaac Sim
from a pip index, whereas the Linux/ARM CI runs inside the internal develop
container. To keep the native path on the same develop build instead of the
older public release, this tool:

1. reads the PEP 503 *simple* index page for the ``isaacsim`` project on the
   internal Artifactory registry,
2. selects the newest pre-release wheel built for the requested Python/platform
   tag (the index also carries release-line builds, so newest alone is not a
   proof of provenance), and
3. optionally verifies that the selected build's embedded git commit is on the
   ``omni_isaac_sim`` develop branch -- the actual "is this develop?" check --
   walking from newest to older until one verifies,

then prints the full version string (e.g. ``6.0.0rc48+release.40557.63231095.gl``)
on stdout for use in ``uv pip install --pre "isaacsim[all,extscache]==<version>"``.

Everything except :func:`_http_get` and :func:`commit_on_branch` is pure and
unit tested in ``tools/test_resolve_isaacsim_develop.py``. Progress/warnings go
to stderr so the resolved version is the only thing on stdout.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

# Wheel filename: isaacsim-<version>-<pytag>-<abitag>-<platformtag>.whl. The
# version segment carries no '-', so a greedy non-'-' run captures it (incl. the
# PEP 440 local segment '+release.<build>.<sha>.gl').
_WHEEL_RE = re.compile(
    r"isaacsim-(?P<version>[^-]+)-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-/\"<>]+)\.whl",
    re.IGNORECASE,
)
# Local build segment baked into internal builds: +release.<build>.<sha>.gl
_BUILD_RE = re.compile(r"\+release\.(?P<build>\d+)\.(?P<sha>[0-9a-fA-F]+)\.gl")


@dataclass(frozen=True)
class IsaacSimWheel:
    """A single ``isaacsim`` wheel parsed from a simple-index page.

    Attributes:
        version: Full PEP 440 version, e.g. ``6.0.0rc48+release.40557.63231095.gl``.
        python_tag: CPython tag from the filename, e.g. ``cp312``.
        platform_tag: Platform tag from the filename, e.g. ``win_amd64``.
        build: Monotonic Isaac Sim build number from the local segment, or ``None``
            for a public build that carries no ``+release.<build>...`` segment.
        commit: ``omni_isaac_sim`` git short SHA from the local segment, or ``None``.
    """

    version: str
    python_tag: str
    platform_tag: str
    build: int | None
    commit: str | None


def parse_simple_index(html: str) -> list[IsaacSimWheel]:
    """Parse a PEP 503 simple-index page into the ``isaacsim`` wheels it lists.

    Args:
        html: Raw HTML of the simple-index project page. URL-encoded ``+`` (``%2B``)
            in hrefs is tolerated by unquoting before matching.

    Returns:
        One :class:`IsaacSimWheel` per distinct wheel filename, in page order.
    """
    text = urllib.parse.unquote(html)
    wheels: list[IsaacSimWheel] = []
    seen: set[str] = set()
    for match in _WHEEL_RE.finditer(text):
        filename = match.group(0)
        if filename in seen:
            continue
        seen.add(filename)
        version = match.group("version")
        build_match = _BUILD_RE.search(version)
        wheels.append(
            IsaacSimWheel(
                version=version,
                python_tag=match.group("py").lower(),
                platform_tag=match.group("plat").lower(),
                build=int(build_match.group("build")) if build_match else None,
                commit=build_match.group("sha").lower() if build_match else None,
            )
        )
    return wheels


def select_candidates(
    wheels: list[IsaacSimWheel],
    python_tag: str,
    platform_tag: str,
    version_prefix: str | None = None,
) -> list[IsaacSimWheel]:
    """Internal builds matching one Python/platform tag, newest build first.

    Public wheels (no ``+release.<build>`` segment) are excluded since only the
    internal builds track the develop branch.

    Args:
        wheels: Parsed wheels from :func:`parse_simple_index`.
        python_tag: Required CPython tag, e.g. ``cp312``.
        platform_tag: Required platform tag, e.g. ``win_amd64``.
        version_prefix: Optional ``str.startswith`` filter on the version, used as
            a coarse develop-line heuristic (e.g. ``6.0.0``) when branch
            verification is unavailable.

    Returns:
        Matching wheels sorted by descending build number (the monotonic CI
        counter, the most reliable "latest develop" ordering).
    """
    python_tag = python_tag.lower()
    platform_tag = platform_tag.lower()
    out = [
        w
        for w in wheels
        if w.python_tag == python_tag
        and w.platform_tag == platform_tag
        and w.build is not None
        and (version_prefix is None or w.version.startswith(version_prefix))
    ]
    out.sort(key=lambda w: w.build or 0, reverse=True)  # builds are filtered non-None above
    return out


def _basic_auth_header(username: str, password: str) -> str:
    """Return the value for an HTTP ``Authorization: Basic`` header for the given credentials."""
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return f"Basic {encoded}"


def _http_get(
    url: str,
    token: str | None = None,
    basic_auth: tuple[str, str] | None = None,
    timeout: float = 30.0,
) -> str:
    """GET ``url`` and return the decoded body. Raises on network/HTTP error."""
    headers = {"User-Agent": "isaaclab-ci-resolve"}
    if token:
        headers["PRIVATE-TOKEN"] = token
    # The internal Artifactory index dropped anonymous access, so the simple-index
    # fetch now needs the read-only service-account credentials (see windows-ci.yaml).
    if basic_auth is not None:
        headers["Authorization"] = _basic_auth_header(*basic_auth)
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (trusted internal URL)
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def commit_on_branch(
    gitlab_base: str,
    project: str,
    commit: str,
    branch: str,
    token: str | None = None,
    timeout: float = 30.0,
) -> bool | None:
    """Whether ``commit`` is on ``branch`` of the gitlab ``project``.

    Args:
        gitlab_base: gitlab base URL, e.g. ``https://gitlab-master.nvidia.com``.
        project: URL path of the project, e.g. ``omniverse/isaac/omni_isaac_sim``.
        commit: Full or short commit SHA to look up.
        branch: Branch name to require, e.g. ``develop``.
        token: gitlab access token (``PRIVATE-TOKEN``); required for private repos.
        timeout: Per-request timeout in seconds.

    Returns:
        ``True``/``False`` when the answer is known, or ``None`` when gitlab could
        not be reached or the response was unusable (caller decides how to degrade).
    """
    encoded_project = urllib.parse.quote(project, safe="")
    url = (
        f"{gitlab_base.rstrip('/')}/api/v4/projects/{encoded_project}"
        f"/repository/commits/{commit}/refs?type=branch&per_page=100"
    )
    try:
        body = _http_get(url, token=token, timeout=timeout)
    except (urllib.error.URLError, OSError):
        return None
    try:
        refs = json.loads(body)
    except ValueError:
        return None
    if not isinstance(refs, list):
        return None
    return any(isinstance(ref, dict) and ref.get("name") == branch for ref in refs)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Prints the resolved version on success; see module docstring."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--index-url",
        action="append",
        required=True,
        metavar="URL",
        help="simple-index 'isaacsim' project page URL (repeatable).",
    )
    parser.add_argument(
        "--index-username",
        default=os.environ.get("ISAACSIM_ARTIFACTORY_READONLY_USERNAME"),
        help="basic-auth username for the index (default: $ISAACSIM_ARTIFACTORY_READONLY_USERNAME).",
    )
    parser.add_argument(
        "--index-password",
        default=os.environ.get("ISAACSIM_ARTIFACTORY_READONLY_PASSWORD"),
        help="basic-auth password for the index (default: $ISAACSIM_ARTIFACTORY_READONLY_PASSWORD).",
    )
    parser.add_argument("--python-tag", default="cp312", help="required CPython tag (default: cp312).")
    parser.add_argument("--platform-tag", default="win_amd64", help="required platform tag (default: win_amd64).")
    parser.add_argument(
        "--version-prefix",
        default=None,
        help="coarse develop-line filter (e.g. 6.0.0); also the fallback when branch verify is unavailable.",
    )
    parser.add_argument(
        "--verify-branch",
        default=None,
        metavar="BRANCH",
        help="require the build's commit to be on this omni_isaac_sim branch (e.g. develop).",
    )
    parser.add_argument("--gitlab-base", default="https://gitlab-master.nvidia.com")
    parser.add_argument("--gitlab-project", default="omniverse/isaac/omni_isaac_sim")
    parser.add_argument(
        "--gitlab-token",
        default=os.environ.get("GITLAB_TOKEN"),
        help="gitlab token for branch verification (default: $GITLAB_TOKEN).",
    )
    parser.add_argument("--max-verify", type=int, default=10, help="max newest builds to branch-check (default: 10).")
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="if gitlab is unreachable, fall back to the newest version-prefix build with a warning.",
    )
    args = parser.parse_args(argv)

    # Both credentials must be present to authenticate; otherwise fetch anonymously.
    index_auth = (args.index_username, args.index_password) if args.index_username and args.index_password else None

    wheels: list[IsaacSimWheel] = []
    for url in args.index_url:
        try:
            wheels.extend(parse_simple_index(_http_get(url, basic_auth=index_auth)))
        except (urllib.error.URLError, OSError) as exc:
            print(f"warning: failed to fetch {url}: {exc}", file=sys.stderr)

    candidates = select_candidates(wheels, args.python_tag, args.platform_tag, args.version_prefix)
    if not candidates:
        print(
            f"error: no isaacsim {args.python_tag}/{args.platform_tag} builds found on the given index"
            f"{f' matching {args.version_prefix}*' if args.version_prefix else ''}",
            file=sys.stderr,
        )
        return 2

    if not args.verify_branch:
        print(candidates[0].version)
        return 0

    for wheel in candidates[: args.max_verify]:
        verdict = commit_on_branch(
            args.gitlab_base, args.gitlab_project, wheel.commit or "", args.verify_branch, token=args.gitlab_token
        )
        if verdict is True:
            print(f"verified {wheel.version} on '{args.verify_branch}'", file=sys.stderr)
            print(wheel.version)
            return 0
        if verdict is None:
            # gitlab unreachable / unusable response -> stop probing, decide fallback.
            if args.allow_unverified:
                print(
                    f"warning: could not reach gitlab to verify '{args.verify_branch}'; falling back to newest"
                    f"{f' {args.version_prefix}' if args.version_prefix else ''} build {candidates[0].version}"
                    " (UNVERIFIED).",
                    file=sys.stderr,
                )
                print(candidates[0].version)
                return 0
            print(
                f"error: could not reach gitlab to verify '{args.verify_branch}'; "
                "pass --allow-unverified to proceed on the version-prefix heuristic.",
                file=sys.stderr,
            )
            return 3
        # verdict is False -> this build is not on the branch; try the next older one.

    print(
        f"error: none of the newest {args.max_verify} isaacsim builds are on '{args.verify_branch}'",
        file=sys.stderr,
    )
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
