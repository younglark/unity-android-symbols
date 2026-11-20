"""Unity release catalog.

Resolves every release in the configured streams to a version, a revision
(the ``shortRevision`` used in Unity's download URLs), and the Android support
package that contains the il2cpp Development arm64-v8a player.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

API = "https://services.api.unity.com/unity/editor/release/v1/releases"
DOWNLOAD = "https://download.unity3d.com/download_unity"
# unity.com sends some networks to a regional landing page; the /en/ locale
# path is tried first, then the bare path.
RELEASE_PAGES = (
    "https://unity.com/en/releases/editor/whats-new/{version}",
    "https://unity.com/releases/editor/whats-new/{version}",
)
STREAMS = ("LTS", "SUPPORTED")
_VERSION = re.compile(r"\d{1,4}\.\d+\.\d+[abfp]\d+")
_REVISION = re.compile(r"[0-9a-z]{12}")


@dataclass(frozen=True)
class Release:
    version: str
    revision: str
    stream: str
    release_date: str
    package_url: str | None
    package_size: int | None

    @property
    def tag(self) -> str:
        return f"unity-{self.version}-{self.revision}"


def _http_json(url: str, timeout: float = 30.0) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "unity-android-symbols"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _http_text(url: str, timeout: float = 30.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "unity-android-symbols"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def resolve_archived_release(
    version: str, revision: str | None = None, fetch=_http_text
) -> Release | None:
    """Resolve a version the release API does not list.

    Extended-support ("3-year LTS") patches, such as 2021.3.57f2, are missing
    from the public API but still published on unity.com and downloadable from
    the CDN. The changeset can be passed directly (it is the second half of the
    ``unityhub://`` install link); otherwise it is read from the release page.
    """
    if not _VERSION.fullmatch(version):
        return None

    if not revision:
        hub = None
        page = ""
        for template in RELEASE_PAGES:
            try:
                page = fetch(template.format(version=version))
            except OSError:
                continue
            hub = re.search(rf"unityhub://{re.escape(version)}/([0-9a-z]{{12}})", page)
            if hub is not None:
                break
        if hub is None:
            return None
        revision = hub.group(1)
        package = re.search(
            rf"https://download\.unity3d\.com/download_unity/{revision}/"
            rf"MacEditorTargetInstaller/UnitySetup-Android-Support-for-Editor-"
            rf"{re.escape(version)}\.pkg",
            page,
        )
        package_url = package.group(0) if package else None
    else:
        if not _REVISION.fullmatch(revision):
            return None
        package_url = (
            f"{DOWNLOAD}/{revision}/MacEditorTargetInstaller/"
            f"UnitySetup-Android-Support-for-Editor-{version}.pkg"
        )

    return Release(
        version=version,
        revision=revision,
        stream="EXTENDED",
        release_date="",
        package_url=package_url,
        package_size=None,
    )


def _android_package(item: dict) -> tuple[str | None, int | None]:
    """Return the Android support package URL and size.

    The Windows module is an ``EXE`` whose NSIS payload 7-Zip cannot unpack, so
    the ``PKG`` module (shared by the macOS and Linux editors) is preferred.
    """
    for download in item.get("downloads", []):
        for module in download.get("modules", []):
            if module.get("id") == "android" and module.get("type") == "PKG":
                size = (module.get("downloadSize") or {}).get("value")
                return module.get("url"), size
    return None, None


def _to_release(item: dict) -> Release | None:
    version = item.get("version")
    revision = item.get("shortRevision")
    if not version or not revision:
        return None
    package_url, package_size = _android_package(item)
    return Release(
        version=version,
        revision=revision,
        stream=item.get("stream", ""),
        release_date=item.get("releaseDate", ""),
        package_url=package_url,
        package_size=package_size,
    )


def fetch_releases(streams=STREAMS, fetch=_http_json, page_size: int = 25) -> list[Release]:
    """Return distinct releases across all streams, newest first.

    The Unity API rejects ``limit`` above 25.
    """
    found: dict[str, Release] = {}
    for stream in streams:
        offset = 0
        while True:
            query = urllib.parse.urlencode(
                {
                    "limit": page_size,
                    "offset": offset,
                    "order": "RELEASE_DATE_DESC",
                    "stream": stream,
                }
            )
            data = fetch(f"{API}?{query}")
            results = data.get("results", [])
            for item in results:
                release = _to_release(item)
                if release is None:
                    continue
                previous = found.get(release.version)
                if previous is None or (previous.package_url is None and release.package_url):
                    found[release.version] = release
            offset += page_size
            if not results or offset >= data.get("total", 0):
                break
    return sorted(found.values(), key=lambda release: release.release_date, reverse=True)


def select_pending(releases, is_complete, limit: int) -> list[Release]:
    """Newest-first releases that still need publishing, capped at ``limit``.

    ``is_complete`` receives a :class:`Release` and returns True when every
    asset for that release already exists.
    """
    pending = []
    for release in releases:
        if release.package_url is None:
            continue
        if is_complete(release):
            continue
        pending.append(release)
        if len(pending) >= limit:
            break
    return pending
