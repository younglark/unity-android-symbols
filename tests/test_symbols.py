import shutil
import subprocess
import sys

import pytest

from unity_symbols.symbols import build_document, collect_symbols, parse_symbols

C_SOURCE = """
int alpha(int x) { return x + 1; }
int beta(int x) { return x * 2; }
static int gamma(int x) { return x - 1; }
int alpha_caller(int x) { return alpha(gamma(x)); }
"""


def test_collect_symbols_prefers_global_over_local_and_records_duplicates():
    entries = [
        ("keep", 0x10, "STT_FUNC", "STB_GLOBAL"),
        ("dup", 0x20, "STT_FUNC", "STB_LOCAL"),
        ("dup", 0x30, "STT_FUNC", "STB_GLOBAL"),
        ("dropped", 0x40, "STT_NOTYPE", "STB_GLOBAL"),
        ("zero", 0x0, "STT_FUNC", "STB_GLOBAL"),
        ("", 0x50, "STT_FUNC", "STB_GLOBAL"),
    ]

    symbols, duplicates = collect_symbols(entries)

    assert symbols == {"keep": "0x10", "dup": "0x30"}
    assert {"name": "dup", "addr": "0x20"} in duplicates
    assert {"name": "dup", "addr": "0x30"} in duplicates


@pytest.mark.skipif(sys.platform != "linux", reason="needs an ELF toolchain")
def test_parse_symbols_matches_nm(tmp_path):
    if shutil.which("cc") is None:
        pytest.skip("no C compiler")

    source = tmp_path / "tiny.c"
    source.write_text(C_SOURCE)
    library = tmp_path / "libtiny.so"
    subprocess.run(["cc", "-shared", "-fPIC", "-o", str(library), str(source)], check=True)

    symbols, _ = parse_symbols(library)

    nm = subprocess.run(
        ["nm", "--defined-only", str(library)], capture_output=True, text=True, check=True
    )
    oracle = {}
    for line in nm.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            oracle[parts[-1]] = int(parts[0], 16)

    for name in ("alpha", "beta", "alpha_caller"):
        assert name in symbols
        assert symbols[name] == hex(oracle[name])


def test_parse_symbols_rejects_non_elf(tmp_path):
    bogus = tmp_path / "not-an-elf.so"
    bogus.write_bytes(b"not an elf")

    with pytest.raises(Exception):
        parse_symbols(bogus)


def test_build_document_carries_metadata_and_hashes(tmp_path):
    lib = tmp_path / "libunity.so"
    lib.write_bytes(b"lib-bytes")
    sym = tmp_path / "libunity.sym.so"
    sym.write_bytes(b"sym-bytes")

    document = build_document(
        version="2022.3.62f1",
        revision="aaa",
        lib_path=lib,
        sym_path=sym,
        symbols={"alpha": "0x10"},
        duplicates=[],
    )

    assert document["unity_version"] == "2022.3.62f1"
    assert document["short_revision"] == "aaa"
    assert document["abi"] == "arm64-v8a"
    assert document["variant"] == "il2cpp/Development"
    assert document["symbol_count"] == 1
    assert len(document["lib_sha256"]) == 64
    assert len(document["sym_sha256"]) == 64
    assert document["symbols"] == {"alpha": "0x10"}
