import urllib.parse

import pytest

from unity_symbols.catalog import fetch_releases, resolve_archived_release, select_pending


def make_item(version, revision, date, *, android=True, pkg=True, stream="LTS"):
    modules = []
    if android:
        if pkg:
            modules.append(
                {
                    "id": "android",
                    "type": "PKG",
                    "url": f"https://download/{revision}.pkg",
                    "downloadSize": {"value": 677},
                }
            )
        modules.append(
            {
                "id": "android",
                "type": "EXE",
                "url": f"https://download/{revision}.exe",
                "downloadSize": {"value": 477},
            }
        )
    return {
        "version": version,
        "shortRevision": revision,
        "stream": stream,
        "releaseDate": date,
        "downloads": [{"platform": "WINDOWS", "modules": modules}],
    }


PAGES = {
    ("LTS", 0): {
        "total": 2,
        "results": [
            make_item("2022.3.62f1", "aaa", "2024-02-01T00:00:00Z"),
            make_item("2021.3.45f1", "bbb", "2024-01-01T00:00:00Z"),
        ],
    },
    ("SUPPORTED", 0): {
        "total": 2,
        "results": [
            make_item("2022.3.62f1", "aaa", "2024-02-01T00:00:00Z", stream="SUPPORTED"),
            make_item("6000.0.85f2", "ccc", "2024-03-01T00:00:00Z", pkg=False, stream="SUPPORTED"),
        ],
    },
}


def fake_fetch(url):
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return PAGES[(query["stream"][0], int(query["offset"][0]))]


def test_fetch_releases_prefers_pkg_dedupes_streams_and_orders_newest_first():
    releases = fetch_releases(streams=("LTS", "SUPPORTED"), fetch=fake_fetch)

    assert [release.version for release in releases] == [
        "6000.0.85f2",
        "2022.3.62f1",
        "2021.3.45f1",
    ]
    by_version = {release.version: release for release in releases}
    # EXE-only releases are not publishable: 7-Zip cannot unpack the NSIS payload.
    assert by_version["6000.0.85f2"].package_url is None
    assert by_version["2022.3.62f1"].package_url == "https://download/aaa.pkg"
    assert by_version["2022.3.62f1"].package_size == 677
    assert by_version["2022.3.62f1"].tag == "unity-2022.3.62f1-aaa"


def test_select_pending_skips_complete_and_versions_without_a_pkg():
    releases = fetch_releases(streams=("LTS", "SUPPORTED"), fetch=fake_fetch)
    complete = {"2022.3.62f1"}

    pending = select_pending(releases, lambda release: release.version in complete, limit=5)

    assert [release.version for release in pending] == ["2021.3.45f1"]


def test_select_pending_applies_limit():
    releases = fetch_releases(streams=("LTS", "SUPPORTED"), fetch=fake_fetch)

    pending = select_pending(releases, lambda release: False, limit=1)

    assert [release.version for release in pending] == ["2022.3.62f1"]


RELEASE_PAGE = """
<a href="unityhub://2021.3.57f2/45bc4c630002">Install</a>
<a href="https://download.unity3d.com/download_unity/45bc4c630002/MacEditorTargetInstaller/UnitySetup-Android-Support-for-Editor-2021.3.57f2.pkg">Android</a>
"""


def test_resolve_archived_release_reads_changeset_and_android_package():
    release = resolve_archived_release("2021.3.57f2", fetch=lambda url: RELEASE_PAGE)

    assert release.version == "2021.3.57f2"
    assert release.revision == "45bc4c630002"
    assert release.package_url == (
        "https://download.unity3d.com/download_unity/45bc4c630002/"
        "MacEditorTargetInstaller/UnitySetup-Android-Support-for-Editor-2021.3.57f2.pkg"
    )
    assert release.tag == "unity-2021.3.57f2-45bc4c630002"


def test_resolve_archived_release_marks_versions_without_an_android_package():
    page = '<a href="unityhub://2021.3.57f2/45bc4c630002">Install</a>'

    release = resolve_archived_release("2021.3.57f2", fetch=lambda url: page)

    assert release.revision == "45bc4c630002"
    assert release.package_url is None


def test_resolve_archived_release_falls_back_when_a_locale_serves_the_wrong_page():
    requested = []

    def fetch(url):
        requested.append(url)
        if "/en/" in url:
            return "<html>regional landing page</html>"
        return RELEASE_PAGE

    release = resolve_archived_release("2021.3.57f2", fetch=fetch)

    assert release.revision == "45bc4c630002"
    assert requested == [
        "https://unity.com/en/releases/editor/whats-new/2021.3.57f2",
        "https://unity.com/releases/editor/whats-new/2021.3.57f2",
    ]


def test_resolve_archived_release_uses_a_given_changeset_without_fetching():
    release = resolve_archived_release(
        "2021.3.57f2",
        revision="45bc4c630002",
        fetch=lambda url: pytest.fail("must not fetch"),
    )

    assert release.revision == "45bc4c630002"
    assert release.package_url == (
        "https://download.unity3d.com/download_unity/45bc4c630002/"
        "MacEditorTargetInstaller/UnitySetup-Android-Support-for-Editor-2021.3.57f2.pkg"
    )


def test_resolve_archived_release_treats_an_empty_changeset_as_missing():
    release = resolve_archived_release(
        "2021.3.57f2", revision="", fetch=lambda url: RELEASE_PAGE
    )

    assert release.revision == "45bc4c630002"


def test_resolve_archived_release_rejects_a_malformed_changeset():
    assert resolve_archived_release(
        "2021.3.57f2", revision="../../etc", fetch=lambda url: pytest.fail("must not fetch")
    ) is None


def test_resolve_archived_release_rejects_unknown_and_malformed_versions():
    assert resolve_archived_release("1999.1.1f1", fetch=lambda url: "<html></html>") is None
    assert resolve_archived_release(
        "../../etc/passwd", fetch=lambda url: pytest.fail("must not fetch")
    ) is None


def test_resolve_archived_release_survives_an_unreachable_page():
    def offline(url):
        raise OSError("network down")

    assert resolve_archived_release("2021.3.57f2", fetch=offline) is None
