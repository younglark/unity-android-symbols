# unity-android-symbols

A periodic, idempotent GitHub Action that publishes the Unity Android
`il2cpp/Development` `arm64-v8a` player library and its symbol table.

Every 30 minutes the workflow lists Unity releases, skips versions whose GitHub
Release is already complete, then downloads, extracts, converts, and publishes
up to two versions, newest first.

## Releases

Each version becomes one GitHub Release tagged
`unity-<version>-<shortRevision>` with two assets:

| Asset | Contents |
| --- | --- |
| `unity-<version>-<rev>.zip` | deterministic zip with the library, symbol library and symbol JSON |
| `manifest.<rev>.json` | member sizes and SHA-256, plus the zip's own size and hash |

Zip members:

| Member | Contents |
| --- | --- |
| `libunity.<rev>.arm64-v8a.so` | `Variations/il2cpp/Development/Libs/arm64-v8a/libunity.so` |
| `libunity.sym.<rev>.arm64-v8a.so` | `Variations/il2cpp/Development/Symbols/arm64-v8a/libunity.sym.so` |
| `symbols.<rev>.json` | symbol name → `0x…` address, plus hashes and counts |

The zip cuts the download from ~164 MB of raw assets to ~50 MB. It uses a fixed
member timestamp, so the same inputs produce the same archive. `symbols.<rev>.json`
keeps the global/weak definition when a name is duplicated; the losing entries
are listed under `duplicate_symbols`.

## Decisions

- **Scope**: `LTS` + `SUPPORTED` streams, no `TECH`/`BETA`/`ALPHA`.
- **No backfill**: history is covered gradually by the schedule (~48 runs a day,
  two versions per run, so about 5 days for the LTS backlog; newest first).
- **Package**: the macOS/Linux `MacEditorTargetInstaller/*.pkg` (~0.68 GB). The
  Windows `TargetSupportInstaller/*.exe` is smaller (~0.48 GB) but its modified
  NSIS payload is not readable by 7-Zip, so the `.pkg` is used.
- **Extraction**: `7z` unpacks the xar container; the nested payload is read as
  gzip or raw odc/newc cpio by a small pure-Python reader that keeps only the two
  `il2cpp/Development/arm64-v8a` files. It tolerates both 7-Zip 23 (`Payload~`,
  gzip already decoded) and 7-Zip 25 (`Payload`, gzip retained).
- **Idempotency**: a release counts as done only when it is published and both
  assets exist with a non-zero size. Incomplete releases are resumed.
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
