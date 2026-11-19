"""Publish Unity Android il2cpp Development libunity + symbol tables.

Run by ``.github/workflows/unity-symbols.yml`` every two hours. Each run:
lists releases, skips versions whose Release is already complete, then
downloads, extracts, converts, and publishes up to ``--limit`` versions,
newest first.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from .catalog import STREAMS, fetch_releases, select_pending
from .extract import DownloadError, download, extract_pkg, find_development_libs
from .publish import ReleaseStore
from .symbols import build_document, parse_symbols


def asset_names(revision: str) -> list[str]:
    return [
        f"libunity.{revision}.arm64-v8a.so",
        f"libunity.sym.{revision}.arm64-v8a.so",
        f"symbols.{revision}.json",
        f"manifest.{revision}.json",
    ]


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_release(release, store, workdir, *, downloader=download, extractor=extract_pkg, finder=find_development_libs, parser=parse_symbols):
    """Produce and publish one release. Returns a result dict for the summary."""
    tag = release.tag
    names = asset_names(release.revision)
    with tempfile.TemporaryDirectory(dir=workdir, prefix=f"{release.revision}-") as tmp:
        tmp = Path(tmp)
        package = downloader(release.package_url, tmp / "android-support.pkg")
        root = extractor(package, tmp / "expanded")
        found = finder(root)
        if found is None:
            return {"version": release.version, "revision": release.revision,
                    "tag": tag, "status": "skipped", "reason": "no il2cpp/Development arm64-v8a files"}
        lib, sym = found

        symbols, duplicates = parser(sym)
        document = build_document(
            version=release.version,
            revision=release.revision,
            lib_path=lib,
            sym_path=sym,
            symbols=symbols,
            duplicates=duplicates,
        )

        output = tmp / "out"
        output.mkdir()
        files = [output / name for name in names]
        files[0].write_bytes(lib.read_bytes())
        files[1].write_bytes(sym.read_bytes())
        files[2].write_text(json.dumps(document, ensure_ascii=False, sort_keys=True))

        manifest = {
            "unity_version": release.version,
            "short_revision": release.revision,
            "tag": tag,
            "variant": document["variant"],
            "abi": document["abi"],
            "symbol_count": document["symbol_count"],
            "files": [
                {"name": path.name, "size": path.stat().st_size, "sha256": _sha256(path)}
                for path in files[:3]
            ],
        }
        files[3].write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))

        store.ensure_draft(tag, f"Unity {release.version} ({release.revision})")
        store.upload(tag, files)
        store.publish(tag)

        return {"version": release.version, "revision": release.revision, "tag": tag,
                "status": "published", "symbols": document["symbol_count"]}


def _write_summary(rows: list[dict], error: str | None = None) -> None:
    lines = ["| Version | Revision | Status | Detail |", "| --- | --- | --- | --- |"]
    for row in rows:
        detail = row.get("reason") or (f"{row['symbols']} symbols" if "symbols" in row else "")
        lines.append(f"| {row['version']} | `{row['revision']}` | {row['status']} | {detail} |")
    if error:
        lines.append("")
        lines.append(f"**Run failed:** {error}")
    text = "\n".join(lines) + "\n"
    print(text)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(text)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=int(os.environ.get("LIMIT", "2")))
    parser.add_argument("--streams", default=",".join(STREAMS))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workdir", default=None)
    args = parser.parse_args(argv)

    streams = tuple(part.strip() for part in args.streams.split(",") if part.strip())
    releases = fetch_releases(streams=streams)
    print(f"catalog: {len(releases)} releases across {streams}")

    if args.dry_run:
        store = None
        pending = [release for release in releases if release.package_url][: args.limit]
    else:
        if not args.repo:
            print("error: --repo or GITHUB_REPOSITORY is required", file=sys.stderr)
            return 2
        store = ReleaseStore(args.repo)
        pending = select_pending(
            releases,
            lambda release: store.is_complete(release.tag, asset_names(release.revision)),
            args.limit,
        )

    if not pending:
        print("nothing to publish")
        _write_summary([])
        return 0

    print("pending: " + ", ".join(release.version for release in pending))
    rows: list[dict] = []
    workdir = Path(args.workdir) if args.workdir else None
    if workdir:
        workdir.mkdir(parents=True, exist_ok=True)

    for release in pending:
        print(f"processing {release.version} ({release.revision})")
        if args.dry_run:
            rows.append({"version": release.version, "revision": release.revision,
                         "tag": release.tag, "status": "would-publish"})
            continue
        try:
            rows.append(process_release(release, store, workdir))
        except DownloadError as error:
            rows.append({"version": release.version, "revision": release.revision,
                         "tag": release.tag, "status": "failed", "reason": str(error)})
            _write_summary(rows, error=str(error))
            return 1

    _write_summary(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
