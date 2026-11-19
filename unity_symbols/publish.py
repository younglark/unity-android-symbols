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
        result = self._gh(["api", f"repos/{self.repo}/releases/tags/{tag}"], check=False)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)

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
