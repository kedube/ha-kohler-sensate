"""Work out the next release from CHANGELOG.md.

The changelog's first ``##`` section decides what happens:

- ``## Unreleased``: the next version is manifest.json's, bumped by the most
  significant ``###`` heading under it (see BUMPS).
- ``## X.Y.Z`` that hasn't been tagged yet: exactly that version.
- A version that's already tagged, or an empty Unreleased section: nothing.

``check`` validates the changelog and prints the plan. ``prepare`` also
renames the section to the new version, sets manifest.json to match and writes
the section out as release notes.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
MANIFEST = ROOT / "custom_components" / "kohler_sensate" / "manifest.json"

MAJOR, MINOR, PATCH = 0, 1, 2
# Keep a Changelog headings (by first word) and the bump each one needs.
BUMPS = {
    "breaking": MAJOR,
    "removed": MAJOR,
    "added": MINOR,
    "changed": MINOR,
    "deprecated": MINOR,
    "fixed": PATCH,
    "security": PATCH,
}

VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
MANIFEST_VERSION = re.compile(r'("version":\s*")([^"]*)(")')
SECTION = re.compile(r"^## ", re.MULTILINE)
SECTION_TITLE = re.compile(r"^## .*$", re.MULTILINE)
SUBSECTION = re.compile(r"^### (.+)$", re.MULTILINE)


class ReleaseError(Exception):
    """The changelog or manifest isn't ready to release."""


@dataclass(frozen=True)
class Release:
    """A version to publish and its release notes."""

    version: str
    notes: str


def parse_version(text: str) -> tuple[int, int, int]:
    """Parse ``X.Y.Z`` into numbers that compare correctly."""
    match = VERSION.fullmatch(text)
    if match is None:
        raise ReleaseError(f"'{text}' is not a version like 1.2.3")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def bump(version: str, level: int) -> str:
    """Return ``version`` bumped by ``level``."""
    major, minor, patch = parse_version(version)
    if level == MAJOR and major == 0:
        level = MINOR  # Before 1.0, breaking changes bump the minor version.
    if level == MAJOR:
        return f"{major + 1}.0.0"
    if level == MINOR:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def bump_level(notes: str) -> int:
    """Return the most significant bump the section's headings call for."""
    allowed = ", ".join(heading.capitalize() for heading in BUMPS)
    headings = SUBSECTION.findall(notes)
    if not headings:
        raise ReleaseError(f"Put changelog entries under ### headings: {allowed}")
    levels = []
    for heading in headings:
        level = BUMPS.get(heading.split()[0].lower())
        if level is None:
            raise ReleaseError(
                f"Unknown changelog heading '### {heading}'. Use one of: {allowed}"
            )
        levels.append(level)
    return min(levels)


def top_section(changelog: str) -> tuple[str, str]:
    """Return the first ``##`` section's title and the text under it."""
    sections = SECTION.split(changelog)
    if len(sections) < 2:
        raise ReleaseError("CHANGELOG.md has no '## ' section")
    title, _, notes = sections[1].partition("\n")
    return title.strip(), notes.strip()


def plan(changelog: str, current: str, released: set[str]) -> Release | None:
    """Return the release the changelog calls for, if any.

    ``current`` is manifest.json's version and ``released`` the versions
    already tagged.
    """
    title, notes = top_section(changelog)
    if title.lower() == "unreleased":
        if not notes:
            return None
        version = bump(current, bump_level(notes))
        if version in released:
            raise ReleaseError(
                f"{version} is already released; manifest.json says {current}"
            )
        return Release(version, notes)

    parse_version(title)
    if title in released:
        return None
    bump_level(notes)  # validates the headings
    newest = max(released, key=parse_version, default=None)
    if newest is not None and parse_version(title) <= parse_version(newest):
        raise ReleaseError(f"{title} is older than the latest release, {newest}")
    return Release(title, notes)


def apply(changelog: str, manifest: str, release: Release) -> tuple[str, str]:
    """Return the changelog and manifest updated for ``release``."""
    changelog = SECTION_TITLE.sub(f"## {release.version}", changelog, count=1)
    manifest, found = MANIFEST_VERSION.subn(
        rf"\g<1>{release.version}\g<3>", manifest, count=1
    )
    if not found:
        raise ReleaseError("manifest.json has no version")
    return changelog, manifest


def manifest_version(manifest: str) -> str:
    """Return the version in manifest.json."""
    match = MANIFEST_VERSION.search(manifest)
    if match is None:
        raise ReleaseError("manifest.json has no version")
    return match.group(2)


def released_versions() -> set[str]:
    """Return the versions already tagged ``vX.Y.Z``."""
    tags = subprocess.run(
        ["git", "tag", "--list", "v*"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return {tag[1:] for tag in tags if VERSION.fullmatch(tag[1:])}


def main() -> int:
    """Run the command line."""
    parser = argparse.ArgumentParser(
        description="Plan or prepare the next release from CHANGELOG.md."
    )
    parser.add_argument("command", choices=("check", "prepare"))
    parser.add_argument(
        "--notes", type=Path, help="prepare: file to write the release notes to"
    )
    args = parser.parse_args()

    changelog = CHANGELOG.read_text(encoding="utf-8")
    manifest = MANIFEST.read_text(encoding="utf-8")
    try:
        release = plan(changelog, manifest_version(manifest), released_versions())
        if release is not None and args.command == "prepare":
            changelog, manifest = apply(changelog, manifest, release)
    except ReleaseError as err:
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error file=CHANGELOG.md::{err}")
        else:
            print(f"error: {err}", file=sys.stderr)
        return 1

    if release is None:
        print("Nothing to release.")
        return 0
    print(f"Next release: {release.version}")
    if args.command == "check":
        return 0

    CHANGELOG.write_text(changelog, encoding="utf-8")
    MANIFEST.write_text(manifest, encoding="utf-8")
    if args.notes:
        args.notes.write_text(release.notes + "\n", encoding="utf-8")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as file:
            file.write(f"version={release.version}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
