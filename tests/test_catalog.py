import urllib.parse

from unity_symbols.catalog import fetch_releases, select_pending


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
