import io
from pathlib import Path

import pytest

from unity_symbols.extract import (
    DownloadError,
    ExtractionError,
    development_artifact,
    download,
    extract_cpio,
    extract_pkg,
    find_development_libs,
)

LIB = "Variations/il2cpp/Development/Libs/arm64-v8a/libunity.so"
SYM = "Variations/il2cpp/Development/Symbols/arm64-v8a/libunity.sym.so"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def newc_entry(name, data=b"", mode=0o100644, ino=1):
    fields = [ino, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name) + 1, 0]
    header = b"070701" + b"".join(f"{value:08x}".encode() for value in fields)
    entry = header + name.encode() + b"\x00"
    entry += b"\x00" * ((4 - len(entry) % 4) % 4)
    entry += data
    entry += b"\x00" * ((4 - len(data) % 4) % 4)
    return entry


def make_cpio(entries):
    return b"".join(entries) + newc_entry("TRAILER!!!")


ODC_WIDTHS = (6, 6, 6, 6, 6, 6, 6, 11, 6, 11)


def odc_entry(name, data=b"", mode=0o100644, ino=1):
    values = (0, ino, mode, 0, 0, 1, 0, 0, len(name) + 1, len(data))
    header = b"070707" + b"".join(
        f"{value:0{width}o}".encode() for value, width in zip(values, ODC_WIDTHS)
    )
    return header + name.encode() + b"\x00" + data


def make_odc(entries):
    return b"".join(entries) + odc_entry("TRAILER!!!")


def test_download_retries_then_succeeds(tmp_path):
    attempts = {"count": 0}
    sleeps = []

    def opener(request, timeout):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise OSError("connection reset")
        return FakeResponse(b"package-bytes")

    destination = download(
        "https://download/package.pkg",
        tmp_path / "package.pkg",
        attempts=3,
        backoff=(1, 2),
        opener=opener,
        sleep=sleeps.append,
    )

    assert destination.read_bytes() == b"package-bytes"
    assert attempts["count"] == 3
    assert sleeps == [1, 2]


def test_download_gives_up_after_all_attempts(tmp_path):
    sleeps = []

    def opener(request, timeout):
        raise OSError("boom")

    with pytest.raises(DownloadError):
        download(
            "https://download/package.pkg",
            tmp_path / "package.pkg",
            attempts=2,
            backoff=(5,),
            opener=opener,
            sleep=sleeps.append,
        )

    assert sleeps == [5]


def test_extract_cpio_writes_only_wanted_entries(tmp_path):
    payload = make_cpio(
        [
            newc_entry(LIB, b"LIB"),
            newc_entry("Variations/other/big.bin", b"x" * 1000),
            newc_entry(SYM, b"SYM"),
        ]
    )

    written = extract_cpio(io.BytesIO(payload), tmp_path, wanted=development_artifact)

    assert written == 2
    assert (tmp_path / LIB).read_bytes() == b"LIB"
    assert (tmp_path / SYM).read_bytes() == b"SYM"
    assert not (tmp_path / "Variations/other/big.bin").exists()


def test_extract_cpio_handles_odc_archives(tmp_path):
    # Unity's pkg Payload is odc, not newc; this mirrors the real format.
    payload = make_odc(
        [
            odc_entry(".", mode=0o040755),
            odc_entry(f"./{LIB}", b"LIB"),
            odc_entry("./Variations/other/big.bin", b"x" * 1000),
            odc_entry(f"./{SYM}", b"SYM"),
        ]
    )

    written = extract_cpio(io.BytesIO(payload), tmp_path, wanted=development_artifact)

    assert written == 2
    assert (tmp_path / LIB).read_bytes() == b"LIB"
    assert (tmp_path / SYM).read_bytes() == b"SYM"
    assert not (tmp_path / "Variations/other/big.bin").exists()


def test_extract_cpio_rejects_parent_traversal(tmp_path):
    payload = make_cpio([newc_entry("../../evil.so", b"x")])

    with pytest.raises(ExtractionError):
        extract_cpio(io.BytesIO(payload), tmp_path)


def test_extract_pkg_reads_nested_payload(tmp_path):
    payload = gzip_bytes(
        make_odc(
            [
                odc_entry(".", mode=0o040755),
                odc_entry(f"./{LIB}", b"LIB"),
                odc_entry(f"./{SYM}", b"SYM"),
                odc_entry("./Variations/other/big.bin", b"x" * 1000),
            ]
        )
    )

    def runner(command, **kwargs):
        output = Path(next(part[2:] for part in command if part.startswith("-o")))
        nested = output / "TargetSupport.pkg.tmp"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "Payload").write_bytes(payload)

    root = extract_pkg("android-support.pkg", tmp_path / "expanded", runner=runner)

    assert (root / LIB).read_bytes() == b"LIB"
    assert (root / SYM).read_bytes() == b"SYM"
    assert find_development_libs(root) == (root / LIB, root / SYM)


def test_extract_pkg_accepts_payload_tilde_from_older_7zip(tmp_path):
    # Ubuntu 24.04 ships 7-Zip 23, which extracts the member as "Payload~".
    payload = gzip_bytes(make_odc([odc_entry(f"./{LIB}", b"LIB"), odc_entry(f"./{SYM}", b"SYM")]))

    def runner(command, **kwargs):
        output = Path(next(part[2:] for part in command if part.startswith("-o")))
        nested = output / "TargetSupport.pkg.tmp"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "Payload~").write_bytes(payload)

    root = extract_pkg("android-support.pkg", tmp_path / "expanded", runner=runner)

    assert (root / LIB).read_bytes() == b"LIB"
    assert (root / SYM).read_bytes() == b"SYM"


def test_extract_pkg_accepts_raw_cpio_payload(tmp_path):
    # 7-Zip 23 on Ubuntu decodes the xar gzip encoding and writes raw cpio.
    payload = make_odc([odc_entry(f"./{LIB}", b"LIB"), odc_entry(f"./{SYM}", b"SYM")])

    def runner(command, **kwargs):
        output = Path(next(part[2:] for part in command if part.startswith("-o")))
        nested = output / "TargetSupport.pkg.tmp"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "Payload~").write_bytes(payload)

    root = extract_pkg("android-support.pkg", tmp_path / "expanded", runner=runner)

    assert (root / LIB).read_bytes() == b"LIB"
    assert (root / SYM).read_bytes() == b"SYM"


def test_extract_pkg_requires_payload(tmp_path):
    def runner(command, **kwargs):
        return None

    with pytest.raises(ExtractionError):
        extract_pkg("android-support.pkg", tmp_path / "expanded", runner=runner)


def gzip_bytes(raw):
    import gzip

    return gzip.compress(raw)
