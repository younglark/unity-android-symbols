"""Download and unpack the Unity Android support ``.pkg``.

The Windows ``.exe`` installer is a modified NSIS stub that 7-Zip only reads as
a PE image, so the macOS/Linux ``.pkg`` is used instead. That package is a xar
archive containing a component package whose ``Payload`` is a gzip-compressed
newc cpio archive.
"""

from __future__ import annotations

import gzip
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

DOWNLOAD_ATTEMPTS = 3
BACKOFF_SECONDS = (10, 30, 90)
DOWNLOAD_TIMEOUT = 1800

LIB_SUFFIX = "il2cpp/Development/Libs/arm64-v8a/libunity.so"
SYM_SUFFIX = "il2cpp/Development/Symbols/arm64-v8a/libunity.sym.so"

_NEWC_MAGIC = (b"070701", b"070702")
_ODC_MAGIC = b"070707"
_NEWC_HEADER = 110
# odc fields after the magic: dev, ino, mode, uid, gid, nlink, rdev, mtime,
# namesize, filesize. Widths and offsets are fixed by POSIX.
_ODC_FIELDS = (
    (0, 6),
    (6, 6),
    (12, 6),
    (18, 6),
    (24, 6),
    (30, 6),
    (36, 6),
    (42, 11),
    (53, 6),
    (59, 11),
)


class DownloadError(RuntimeError):
    """Raised when every download attempt for a package failed."""


class ExtractionError(RuntimeError):
    """Raised when the package does not contain the expected payload."""


def development_artifact(name: str) -> bool:
    """True for the two il2cpp Development arm64-v8a files we publish."""
    return name.endswith(LIB_SUFFIX) or name.endswith(SYM_SUFFIX)


def download(
    url: str,
    destination,
    *,
    attempts: int = DOWNLOAD_ATTEMPTS,
    backoff: tuple[int, ...] = BACKOFF_SECONDS,
    timeout: float = DOWNLOAD_TIMEOUT,
    opener=urllib.request.urlopen,
    sleep=time.sleep,
):
    destination = Path(destination)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "unity-android-symbols"}
            )
            with opener(request, timeout=timeout) as response, open(destination, "wb") as handle:
                shutil.copyfileobj(response, handle)
            return destination
        except Exception as error:  # noqa: BLE001 - retried by design
            last_error = error
            if attempt + 1 < attempts:
                sleep(backoff[min(attempt, len(backoff) - 1)])
    raise DownloadError(
        f"download failed after {attempts} attempts: {url}: {last_error}"
    ) from last_error


def _run_7z(archive, destination, runner):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    runner(
        ["7z", "x", "-y", f"-o{destination}", str(archive)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _safe_path(root: Path, name: str) -> Path:
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise ExtractionError(f"unsafe cpio entry: {name!r}")
    return root.joinpath(*parts)


def _discard(stream, size: int) -> None:
    remaining = size
    while remaining > 0:
        chunk = stream.read(min(remaining, 1024 * 1024))
        if not chunk:
            raise ExtractionError("truncated cpio entry")
        remaining -= len(chunk)


def _read_header(stream):
    """Read one cpio header, returning ``(mode, size, namesize, newc)``."""
    magic = stream.read(6)
    if not magic:
        return None
    if magic == _ODC_MAGIC:
        rest = stream.read(70)
        if len(rest) != 70:
            raise ExtractionError("truncated odc header")
        values = [int(rest[start : start + width], 8) for start, width in _ODC_FIELDS]
        return values[2], values[9], values[8], False
    if magic in _NEWC_MAGIC:
        rest = stream.read(104)
        if len(rest) != 104:
            raise ExtractionError("truncated newc header")
        try:
            values = [int(rest[index * 8 : (index + 1) * 8], 16) for index in range(13)]
        except ValueError as error:
            raise ExtractionError("invalid newc header fields") from error
        return values[1], values[6], values[11], True
    raise ExtractionError(f"unsupported cpio magic: {magic!r}")


def extract_cpio(stream, destination, wanted=None) -> int:
    """Extract a cpio stream (odc or newc), optionally skipping entries."""
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    written = 0
    while True:
        header = _read_header(stream)
        if header is None:
            break
        mode, size, namesize, newc = header
        name = stream.read(namesize).rstrip(b"\x00").decode("utf-8", "replace")
        if newc:
            stream.read((-1 * (_NEWC_HEADER + namesize)) % 4)
        if name == "TRAILER!!!":
            break

        file_type = mode & 0o170000
        keep = wanted is None or wanted(name)
        if not keep:
            _discard(stream, size)
            if newc:
                stream.read((-size) % 4)
            continue

        target = _safe_path(root, name)
        if file_type == 0o040000:
            target.mkdir(parents=True, exist_ok=True)
            _discard(stream, size)
        elif file_type == 0o120000:
            link_target = stream.read(size).decode("utf-8", "replace")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.symlink_to(link_target)
            written += 1
        elif file_type == 0o100000:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as handle:
                remaining = size
                while remaining > 0:
                    chunk = stream.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise ExtractionError("truncated cpio entry")
                    handle.write(chunk)
                    remaining -= len(chunk)
            written += 1
        else:
            _discard(stream, size)
        if newc:
            stream.read((-size) % 4)
    return written


def _find_payload(outer: Path) -> Path | None:
    """Find the component payload.

    7-Zip 23 (Ubuntu) writes it as ``Payload~`` while 7-Zip 25 writes
    ``Payload``, so accept both spellings.
    """
    candidates = [
        path for path in outer.rglob("*") if path.is_file() and path.name.startswith("Payload")
    ]
    for preferred in ("Payload", "Payload~"):
        for path in candidates:
            if path.name == preferred:
                return path
    return candidates[0] if candidates else None


def extract_pkg(package, destination, *, runner=subprocess.run, wanted=development_artifact):
    """Unpack the nested package payload and return the extracted files root."""
    destination = Path(destination)
    outer = destination / "outer"
    _run_7z(package, outer, runner)

    payload = _find_payload(outer)
    if payload is None:
        raise ExtractionError(f"no Payload in {package}")

    files = destination / "payload"
    files.mkdir(parents=True, exist_ok=True)
    # 7-Zip 25 stores the payload's gzip bytes; 7-Zip 23 decodes the xar gzip
    # encoding itself and leaves raw cpio. Detect instead of assuming.
    handle = open(payload, "rb")
    try:
        compressed = handle.read(2) == b"\x1f\x8b"
        handle.seek(0)
        stream = gzip.GzipFile(fileobj=handle, mode="rb") if compressed else handle
        try:
            extract_cpio(stream, files, wanted=wanted)
        finally:
            if stream is not handle:
                stream.close()
    finally:
        handle.close()
    return files


def find_development_libs(root):
    """Return ``(libunity.so, libunity.sym.so)`` for il2cpp Development arm64-v8a."""
    root = Path(root)
    lib = next(
        (path for path in root.rglob("libunity.so") if path.as_posix().endswith(LIB_SUFFIX)),
        None,
    )
    if lib is None:
        return None
    sym = next(
        (path for path in root.rglob("libunity.sym.so") if path.as_posix().endswith(SYM_SUFFIX)),
        None,
    )
    if sym is None:
        return None
    return lib, sym
