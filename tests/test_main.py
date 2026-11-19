import json
from pathlib import Path

import pytest

from unity_symbols import main as main_module
from unity_symbols.catalog import Release
from unity_symbols.extract import DownloadError
from unity_symbols.main import asset_names, process_release


def make_release():
    return Release(
        version="2022.3.62f1",
        revision="aaa",
        stream="LTS",
        release_date="2024-02-01T00:00:00Z",
        package_url="https://download/aaa.pkg",
        package_size=677,
    )


class RecordingStore:
    def __init__(self):
        self.events = []
        self.uploaded = {}

    def is_complete(self, tag, asset_names):
        return False

    def create_draft(self, tag, title):
        self.events.append(("draft", tag, title))

    def upload(self, tag, files):
        self.events.append(("upload", tag))
        self.uploaded = {Path(path).name: Path(path).read_bytes() for path in files}

    def publish(self, tag):
        self.events.append(("publish", tag))


def test_process_release_publishes_four_assets_with_manifest(tmp_path):
    release = make_release()
    lib = tmp_path / "libunity.so"
    lib.write_bytes(b"LIB")
    sym = tmp_path / "libunity.sym.so"
    sym.write_bytes(b"SYM")

    def downloader(url, destination):
        assert url == release.package_url
        Path(destination).write_bytes(b"EXE")
        return Path(destination)

    def extractor(installer, destination):
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
    assert sorted(store.uploaded) == sorted(asset_names("aaa"))

    symbols_document = json.loads(store.uploaded["symbols.aaa.json"])
    assert symbols_document["symbols"] == {"alpha": "0x10"}
    assert symbols_document["short_revision"] == "aaa"

    manifest = json.loads(store.uploaded["manifest.aaa.json"])
    assert manifest["unity_version"] == "2022.3.62f1"
    assert manifest["symbol_count"] == 1
    assert {entry["name"] for entry in manifest["files"]} == set(asset_names("aaa")[:3])
    assert all(entry["size"] > 0 and len(entry["sha256"]) == 64 for entry in manifest["files"])


def test_process_release_skips_when_development_variant_is_missing(tmp_path):
    release = make_release()
    store = RecordingStore()

    def downloader(url, destination):
        Path(destination).write_bytes(b"EXE")
        return Path(destination)

    def extractor(installer, destination):
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
            extractor=lambda installer, destination: Path(destination),
            finder=lambda root: None,
            parser=lambda path: ({}, []),
        )


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
