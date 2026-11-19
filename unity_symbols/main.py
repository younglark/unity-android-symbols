"""Publish Unity Android il2cpp Development libunity + symbol tables.

Run by ``.github/workflows/unity-symbols.yml`` every two hours. Each run:
lists releases, skips versions whose Release is already complete, then
downloads, extracts, converts, and publishes up to ``--limit`` versions,
newest first.

Each version is published as one deterministic zip (library, symbol library,
symbol JSON) plus a small manifest asset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

from .catalog import STREAMS, fetch_releases, select_pending
from .extract import DownloadError, download, extract_pkg, find_development_libs
from .publish import ReleaseStore
from .symbols import build_document, parse_symbols

# Fixed timestamp so repeated runs produce byte-identical archives.
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def asset_names(version: str, revision: str) -> list[str]:
    return [f"unity-{version}-{revision}.zip", f"manifest.{revision}.json"]


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_info(arcname: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(arcname, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def _add_file(archive: zipfile.ZipFile, path, arcname: str) -> None:
    with open(path, "rb") as source, archive.open(_zip_info(arcname), "w") as target:
        shutil.copyfileobj(source, target, 1024 * 1024)


def _add_bytes(archive: zipfile.ZipFile, data: bytes, arcname: str) -> None:
    archive.writestr(_zip_info(arcname), data)


def process_release(release, store, workdir, *, downloader=download, extractor=extract_pkg, finder=find_development_libs, parser=parse_symbols):
    """Produce and publish one release. Returns a result dict for the summary."""
    tag = release.tag
    names = asset_names(release.version, release.revision)
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

        revision = release.revision
        members = [
            (lib, f"libunity.{revision}.arm64-v8a.so"),
            (sym, f"libunity.sym.{revision}.arm64-v8a.so"),
        ]
        symbols_json = json.dumps(document, ensure_ascii=False, sort_keys=True).encode()

        output = tmp / "out"
        output.mkdir()
        zip_path = output / names[0]
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for source, arcname in members:
                _add_file(archive, source, arcname)
            _add_bytes(archive, symbols_json, f"symbols.{revision}.json")

        manifest = {
            "unity_version": release.version,
            "short_revision": revision,
            "tag": tag,
            "variant": document["variant"],
            "abi": document["abi"],
            "symbol_count": document["symbol_count"],
            "zip": {
                "name": zip_path.name,
                "size": zip_path.stat().st_size,
                "sha256": _sha256(zip_path),
            },
            "files": [
                {"name": arcname, "size": Path(source).stat().st_size, "sha256": _sha256(source)}
                for source, arcname in members
            ]
            + [
                {
                    "name": f"symbols.{revision}.json",
                    "size": len(symbols_json),
                    "sha256": hashlib.sha256(symbols_json).hexdigest(),
                }
            ],
        }
        manifest_path = output / names[1]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))

        store.ensure_draft(tag, f"Unity {release.version} ({revision})")
        store.upload(tag, [zip_path, manifest_path])
        store.publish(tag)

        return {"version": release.version, "revision": revision, "tag": tag,
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
    parser.add_argument("--version", default=os.environ.get("VERSION") or None,
                        help="process only this Unity version (e.g. 2022.3.62f1)")
    parser.add_argument("--streams", default=",".join(STREAMS))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workdir", default=None)
    args = parser.parse_args(argv)

    streams = tuple(part.strip() for part in args.streams.split(",") if part.strip())
    releases = fetch_releases(streams=streams)
    print(f"catalog: {len(releases)} releases across {streams}")

    if args.version:
        matches = [release for release in releases if release.version == args.version]
        if not matches:
            print(f"error: Unity version not found in the catalog: {args.version}", file=sys.stderr)
            return 1
        requested = matches[0]
        if requested.package_url is None:
            print(f"error: no Android pkg for Unity {requested.version}", file=sys.stderr)
            return 1
        releases_to_consider = [requested]
    else:
        releases_to_consider = releases

    if args.dry_run:
        store = None
        if args.version:
            pending = releases_to_consider
        else:
            pending = [release for release in releases_to_consider if release.package_url][: args.limit]
    else:
        if not args.repo:
            print("error: --repo or GITHUB_REPOSITORY is required", file=sys.stderr)
            return 2
        store = ReleaseStore(args.repo)
        pending = select_pending(
            releases_to_consider,
            lambda release: store.is_complete(
                release.tag, asset_names(release.version, release.revision)
            ),
            args.limit if not args.version else 1,
        )
        if args.version and not pending:
            print(f"Unity {args.version} is already complete; nothing to do")
            _write_summary([])
            return 0

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
