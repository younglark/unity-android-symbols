import io
import json
import zipfile
from pathlib import Path

import pytest

from unity_symbols import main as main_module
from unity_symbols.catalog import Release
from unity_symbols.extract import DownloadError
from unity_symbols.main import asset_names, process_release


def make_release(version="2022.3.62f1", revision="aaa"):
    return Release(
        version=version,
        revision=revision,
        stream="LTS",
        release_date="2024-02-01T00:00:00Z",
        package_url=f"https://download/{revision}.pkg",
        package_size=677,
    )


class RecordingStore:
    def __init__(self, complete=()):
        self.events = []
        self.uploaded = {}
        self.complete = set(complete)

    def is_complete(self, tag, asset_names):
        return tag in self.complete

    def ensure_draft(self, tag, title):
        self.events.append(("draft", tag, title))

    def create_draft(self, tag, title):
        self.events.append(("draft", tag, title))

    def upload(self, tag, files):
        self.events.append(("upload", tag))
        self.uploaded = {Path(path).name: Path(path).read_bytes() for path in files}

    def publish(self, tag):
        self.events.append(("publish", tag))


def test_process_release_publishes_one_zip_and_one_manifest(tmp_path):
    release = make_release()
    lib = tmp_path / "libunity.so"
    lib.write_bytes(b"LIB")
    sym = tmp_path / "libunity.sym.so"
    sym.write_bytes(b"SYM")

    def downloader(url, destination):
        assert url == release.package_url
        Path(destination).write_bytes(b"PKG")
        return Path(destination)

    def extractor(package, destination):
        Path(destination).mkdir(parents=True, exist_ok=True)
        return Path(destination)

    def finder(root):
        assert Path(root).exists()
        return lib, sym

    def parser(path):
        assert Path(path) == sym
        return {"alpha": "0x10"}, []

    store = RecordingStore()
    result = process_release(
        release,
        store,
        tmp_path,
        downloader=downloader,
        extractor=extractor,
        finder=finder,
        parser=parser,
    )

    assert result["status"] == "published"
    assert result["symbols"] == 1
    assert [event[0] for event in store.events] == ["draft", "upload", "publish"]

    names = asset_names("2022.3.62f1", "aaa")
    assert sorted(store.uploaded) == sorted(names)

    with zipfile.ZipFile(io.BytesIO(store.uploaded[names[0]])) as archive:
        assert sorted(archive.namelist()) == [
            "libunity.aaa.arm64-v8a.so",
            "libunity.sym.aaa.arm64-v8a.so",
            "symbols.aaa.json",
        ]
        assert archive.read("libunity.aaa.arm64-v8a.so") == b"LIB"
        assert archive.read("libunity.sym.aaa.arm64-v8a.so") == b"SYM"
        symbols_document = json.loads(archive.read("symbols.aaa.json"))
        assert symbols_document["symbols"] == {"alpha": "0x10"}
        assert symbols_document["short_revision"] == "aaa"

    manifest = json.loads(store.uploaded[names[1]])
    assert manifest["unity_version"] == "2022.3.62f1"
    assert manifest["symbol_count"] == 1
    assert manifest["zip"]["name"] == names[0]
    assert manifest["zip"]["size"] == len(store.uploaded[names[0]])
    assert len(manifest["zip"]["sha256"]) == 64
    assert {entry["name"] for entry in manifest["files"]} == {
        "libunity.aaa.arm64-v8a.so",
        "libunity.sym.aaa.arm64-v8a.so",
        "symbols.aaa.json",
    }
    assert all(len(entry["sha256"]) == 64 and entry["size"] > 0 for entry in manifest["files"])


def test_process_release_is_deterministic(tmp_path):
    release = make_release()
    lib = tmp_path / "libunity.so"
    lib.write_bytes(b"LIB")
    sym = tmp_path / "libunity.sym.so"
    sym.write_bytes(b"SYM")

    def downloader(url, destination):
        Path(destination).write_bytes(b"PKG")
        return Path(destination)

    def run():
        store = RecordingStore()
        process_release(
            release,
            store,
            tmp_path,
            downloader=downloader,
            extractor=lambda package, destination: Path(destination),
            finder=lambda root: (lib, sym),
            parser=lambda path: ({"alpha": "0x10"}, []),
        )
        return store.uploaded[asset_names("2022.3.62f1", "aaa")[0]]

    assert run() == run()


def test_process_release_skips_when_development_variant_is_missing(tmp_path):
    release = make_release()
    store = RecordingStore()

    def downloader(url, destination):
        Path(destination).write_bytes(b"PKG")
        return Path(destination)

    def extractor(package, destination):
        Path(destination).mkdir(parents=True, exist_ok=True)
        return Path(destination)

    result = process_release(
        release,
        store,
        tmp_path,
        downloader=downloader,
        extractor=extractor,
        finder=lambda root: None,
        parser=lambda path: ({}, []),
    )

    assert result["status"] == "skipped"
    assert store.events == []


def test_process_release_propagates_download_failure(tmp_path):
    release = make_release()

    def downloader(url, destination):
        raise DownloadError("download failed after 3 attempts")

    with pytest.raises(DownloadError):
        process_release(
            release,
            RecordingStore(),
            tmp_path,
            downloader=downloader,
            extractor=lambda package, destination: Path(destination),
            finder=lambda root: None,
            parser=lambda path: ({}, []),
        )


def test_main_processes_only_the_requested_version(monkeypatch, capsys):
    releases = [make_release("6000.0.85f2", "bbb"), make_release("2022.3.62f1", "aaa")]
    monkeypatch.setattr(main_module, "fetch_releases", lambda streams: releases)
    monkeypatch.setattr(main_module, "ReleaseStore", lambda repo: RecordingStore())
    processed = []

    def fake_process(release, store, workdir):
        processed.append(release.version)
        return {"version": release.version, "revision": release.revision,
                "tag": release.tag, "status": "published", "symbols": 1}

    monkeypatch.setattr(main_module, "process_release", fake_process)

    exit_code = main_module.main(["--repo", "owner/repo", "--version", "2022.3.62f1"])

    assert exit_code == 0
    assert processed == ["2022.3.62f1"]


def test_main_skips_a_requested_version_that_is_already_complete(monkeypatch, capsys):
    release = make_release()
    monkeypatch.setattr(main_module, "fetch_releases", lambda streams: [release])
    monkeypatch.setattr(main_module, "ReleaseStore", lambda repo: RecordingStore({release.tag}))
    monkeypatch.setattr(
        main_module, "process_release", lambda *args: pytest.fail("must not publish")
    )

    exit_code = main_module.main(["--repo", "owner/repo", "--version", release.version])

    assert exit_code == 0
    assert "already complete" in capsys.readouterr().out


def test_main_rejects_an_unknown_version(monkeypatch, capsys):
    monkeypatch.setattr(main_module, "fetch_releases", lambda streams: [make_release()])
    monkeypatch.setattr(main_module, "resolve_archived_release", lambda version, revision=None: None)

    exit_code = main_module.main(["--repo", "owner/repo", "--version", "1999.1.1f1"])

    assert exit_code == 1
    assert "not found" in capsys.readouterr().err


def test_main_resolves_a_version_missing_from_the_release_api(monkeypatch, capsys):
    release = make_release("2021.3.57f2", "45bc4c630002")
    seen = {}

    def fake_resolve(version, revision=None):
        seen.update(version=version, revision=revision)
        return release

    monkeypatch.setattr(main_module, "fetch_releases", lambda streams: [make_release()])
    monkeypatch.setattr(main_module, "resolve_archived_release", fake_resolve)
    monkeypatch.setattr(main_module, "ReleaseStore", lambda repo: RecordingStore())
    processed = []

    def fake_process(requested, store, workdir):
        processed.append(requested.version)
        return {"version": requested.version, "revision": requested.revision,
                "tag": requested.tag, "status": "published", "symbols": 1}

    monkeypatch.setattr(main_module, "process_release", fake_process)

    exit_code = main_module.main(
        ["--repo", "owner/repo", "--version", "2021.3.57f2", "--revision", ""]
    )

    assert exit_code == 0
    assert processed == ["2021.3.57f2"]
    assert seen == {"version": "2021.3.57f2", "revision": None}
    assert "resolved it from the unity.com release page" in capsys.readouterr().out


def test_main_dry_run_lists_pending_without_network(monkeypatch, capsys):
    releases = [make_release()]
    monkeypatch.setattr(main_module, "fetch_releases", lambda streams: releases)

    exit_code = main_module.main(["--dry-run", "--limit", "1"])

    assert exit_code == 0
    assert "would-publish" in capsys.readouterr().out


def test_main_returns_failure_when_download_retries_are_exhausted(monkeypatch, capsys):
    releases = [make_release()]
    monkeypatch.setattr(main_module, "fetch_releases", lambda streams: releases)
    monkeypatch.setattr(main_module, "ReleaseStore", lambda repo: RecordingStore())

    def failing_process(release, store, workdir):
        raise DownloadError("download failed after 3 attempts")

    monkeypatch.setattr(main_module, "process_release", failing_process)

    exit_code = main_module.main(["--repo", "owner/repo", "--limit", "1"])

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "failed" in output
    assert "download failed after 3 attempts" in output
