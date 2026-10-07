"""Tests for working out releases from the changelog (script/release.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

_PATH = Path(__file__).parent.parent / "script" / "release.py"
_SPEC = importlib.util.spec_from_file_location("release", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
release = importlib.util.module_from_spec(_SPEC)
sys.modules["release"] = release
_SPEC.loader.exec_module(release)

OLDER = """## 0.2.0

### Added

- Something earlier.
"""


def _changelog(top: str) -> str:
    return f"# Changelog\n\n{top}\n{OLDER}"


@pytest.mark.parametrize(
    ("headings", "current", "expected"),
    [
        (["Fixed"], "0.2.0", "0.2.1"),
        (["Security"], "0.2.3", "0.2.4"),
        (["Fixed", "Added"], "0.2.3", "0.3.0"),
        (["Changed"], "1.4.2", "1.5.0"),
        (["Deprecated"], "1.4.2", "1.5.0"),
        (["Removed"], "1.4.2", "2.0.0"),
        (["Fixed", "Breaking changes"], "1.4.2", "2.0.0"),
        # Before 1.0, breaking changes bump the minor version.
        (["Breaking changes"], "0.2.3", "0.3.0"),
    ],
)
def test_unreleased_bump(headings: list[str], current: str, expected: str) -> None:
    body = "".join(f"### {heading}\n\n- An entry.\n\n" for heading in headings)
    planned = release.plan(_changelog(f"## Unreleased\n\n{body}"), current, {"0.2.0"})
    assert planned.version == expected
    assert planned.notes == body.strip()


def test_empty_unreleased_is_nothing_to_release() -> None:
    assert release.plan(_changelog("## Unreleased\n"), "0.2.0", {"0.2.0"}) is None


def test_explicit_version() -> None:
    changelog = _changelog("## 1.0.0\n\n### Changed\n\n- Stable.\n")
    planned = release.plan(changelog, "0.2.0", {"0.2.0"})
    assert planned == release.Release("1.0.0", "### Changed\n\n- Stable.")
    assert release.plan(changelog, "1.0.0", {"0.2.0", "1.0.0"}) is None


def test_first_release_of_current_version() -> None:
    assert release.plan(OLDER, "0.2.0", set()).version == "0.2.0"


@pytest.mark.parametrize(
    ("top", "current", "message"),
    [
        (
            "## Unreleased\n\n### Fixd\n\n- Typo.\n",
            "0.2.0",
            "Unknown changelog heading",
        ),
        ("## Unreleased\n\n- No heading.\n", "0.2.0", "under ### headings"),
        ("## 0.1.9\n\n### Fixed\n\n- Old.\n", "0.2.0", "older than the latest"),
        ("## Next\n\n### Fixed\n\n- x\n", "0.2.0", "not a version"),
        # manifest.json fell behind the tags.
        ("## Unreleased\n\n### Fixed\n\n- x\n", "0.1.9", "already released"),
    ],
)
def test_mistakes_are_reported(top: str, current: str, message: str) -> None:
    released = {"0.1.10", "0.2.0"}
    with pytest.raises(release.ReleaseError, match=message):
        release.plan(_changelog(top), current, released)


def test_versions_compare_numerically() -> None:
    changelog = _changelog("## 0.10.0\n\n### Added\n\n- x\n")
    assert release.plan(changelog, "0.9.0", {"0.9.0"}).version == "0.10.0"


def test_apply_updates_only_the_version() -> None:
    changelog = _changelog("## Unreleased\n\n### Fixed\n\n- A fix.\n")
    manifest = '{\n  "domain": "kohler_sensate",\n  "version": "0.2.0"\n}\n'
    planned = release.Release("0.2.1", "### Fixed\n\n- A fix.")

    new_changelog, new_manifest = release.apply(changelog, manifest, planned)

    assert new_changelog == changelog.replace("## Unreleased", "## 0.2.1")
    assert new_manifest == manifest.replace("0.2.0", "0.2.1")
    assert release.manifest_version(new_manifest) == "0.2.1"


def test_repository_changelog_is_releasable() -> None:
    changelog = release.CHANGELOG.read_text(encoding="utf-8")
    manifest = release.MANIFEST.read_text(encoding="utf-8")
    # Whatever the tags, the changelog's top section must parse.
    release.plan(changelog, release.manifest_version(manifest), set())
