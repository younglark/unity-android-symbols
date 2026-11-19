"""GitHub Release storage with draft-then-publish semantics."""

from __future__ import annotations

import json
import subprocess


class ReleaseStore:
    """Thin wrapper over the ``gh`` CLI.

    A release is complete only when it is published, not a draft, and every
    expected asset is present with a non-zero size. Incomplete releases are
    resumed by re-uploading the missing files, never by deleting the tag.
    """

    def __init__(self, repo: str, *, runner=subprocess.run):
        self.repo = repo
        self.run = runner

    def _gh(self, args, *, check=True):
        return self.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=check,
        )

    def get(self, tag: str):
        """Return the release for ``tag``, including drafts.

        ``GET /releases/tags/{tag}`` omits drafts, so a list lookup is used as a
        fallback; drafts are what an interrupted run leaves behind.
        """
        result = self._gh(["api", f"repos/{self.repo}/releases/tags/{tag}"], check=False)
        if result.returncode == 0:
            return json.loads(result.stdout)
        for page in range(1, 6):
            listing = self._gh(
                ["api", f"repos/{self.repo}/releases?per_page=100&page={page}"], check=False
            )
            if listing.returncode != 0:
                return None
            releases = json.loads(listing.stdout)
            for release in releases:
                if release.get("tag_name") == tag:
                    return release
            if len(releases) < 100:
                break
        return None

    def ensure_draft(self, tag: str, title: str):
        """Create the draft only when no release for the tag exists yet."""
        release = self.get(tag)
        if release is None:
            self.create_draft(tag, title)
            return self.get(tag)
        return release

    def is_complete(self, tag: str, asset_names) -> bool:
        release = self.get(tag)
        if release is None or release.get("draft"):
            return False
        assets = {asset["name"]: asset for asset in release.get("assets", [])}
        return all(
            name in assets and assets[name].get("size", 0) > 0 for name in asset_names
        )

    def create_draft(self, tag: str, title: str) -> None:
        self._gh(
            [
                "release",
                "create",
                tag,
                "--draft",
                "--title",
                title,
                "--notes",
                f"Unity package artifacts for {title}",
            ]
        )

    def upload(self, tag: str, files) -> None:
        self._gh(["release", "upload", tag, *[str(path) for path in files], "--clobber"])

    def publish(self, tag: str) -> None:
        self._gh(["release", "edit", tag, "--draft=false"])
