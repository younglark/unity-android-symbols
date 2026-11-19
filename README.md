# unity-android-symbols

A periodic, idempotent GitHub Action that publishes the Unity Android
`il2cpp/Development` `arm64-v8a` player library and its symbol table.

Every two hours the workflow lists Unity releases, skips versions whose GitHub
Release is already complete, then downloads, extracts, converts, and publishes
up to two versions, newest first.

## Releases

Each version becomes one GitHub Release tagged
`unity-<version>-<shortRevision>` with four assets:

| Asset | Contents |
| --- | --- |
| `libunity.<rev>.arm64-v8a.so` | `Variations/il2cpp/Development/Libs/arm64-v8a/libunity.so` |
| `libunity.sym.<rev>.arm64-v8a.so` | `Variations/il2cpp/Development/Symbols/arm64-v8a/libunity.sym.so` |
| `symbols.<rev>.json` | symbol name → `0x…` address, plus hashes and counts |
| `manifest.<rev>.json` | per-file size and SHA-256 for integrity checks |

`symbols.<rev>.json` keeps the global/weak definition when a name is
duplicated; the losing entries are listed under `duplicate_symbols`.

## Decisions

- **Scope**: `LTS` + `SUPPORTED` streams, no `TECH`/`BETA`/`ALPHA`.
- **No backfill**: history is covered gradually by the schedule (~24 versions a
  day, newest first).
- **Package**: the macOS/Linux `MacEditorTargetInstaller/*.pkg` (~0.68 GB). The
  Windows `TargetSupportInstaller/*.exe` is smaller (~0.48 GB) but its modified
  NSIS payload is not readable by 7-Zip, so the `.pkg` is used.
- **Extraction**: `7z` unpacks the xar container; the nested `Payload` is then
  read as gzip+newc cpio by a small pure-Python reader that keeps only the two
  `il2cpp/Development/arm64-v8a` files.
- **Idempotency**: a release counts as done only when it is published and all
  four assets exist with a non-zero size. Incomplete releases are resumed.
- **Upload**: draft → upload → publish, so consumers never see a partial release.
- **Failure**: a download that fails three times (10 s / 30 s / 90 s backoff,
  30 min timeout) fails the job and leaves the version unpublished for the next
  run. Missing modules or variants are skipped and reported in the job summary.
- **Compliance**: the repository is public and republishes Unity binaries. This
  is a deliberate, risk-accepted choice; the artifacts are removed on request.

## Running locally

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest

# dry run: list what the next scheduled run would publish
.venv/bin/python -m unity_symbols.main --dry-run --limit 2

# real run against a repository with gh authenticated
GH_TOKEN=… .venv/bin/python -m unity_symbols.main --repo owner/repo --limit 2
```

Requires `7z` (`brew install 7zip` or `apt-get install 7zip`) and `gh` for real
publishing. No `cpio` binary is needed; the payload reader is part of the
package.

## Tests

- `tests/test_catalog.py` — stream filtering, dedupe, ordering, per-run cap.
- `tests/test_extract.py` — retry/backoff, layout discovery, 7z invocation.
- `tests/test_symbols.py` — symbol collection and duplicate preference; ELF
  parsing is cross-checked against `nm` on Linux.
- `tests/test_publish.py` — completeness rules and draft/upload/publish order.
- `tests/test_main.py` — end-to-end orchestration of one release with injected
  downloader/extractor/parser, plus CLI failure behaviour.
