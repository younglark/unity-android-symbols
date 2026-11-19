"""Turn ``libunity.sym.so`` into the JSON symbol table consumed by the patcher."""

from __future__ import annotations

import hashlib
from pathlib import Path

from elftools.elf.elffile import ELFFile

SYMBOL_TYPES = ("STT_FUNC", "STT_OBJECT")
GLOBAL_BINDS = ("STB_GLOBAL", "STB_WEAK")
ABI = "arm64-v8a"
VARIANT = "il2cpp/Development"


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_symbols(entries):
    """Collect ``(name, address, type, bind)`` entries into a name -> address map.

    When a name appears more than once, a global or weak binding wins over a
    local one; every losing entry is kept in ``duplicate_symbols``.
    """
    symbols: dict[str, tuple[int, str]] = {}
    duplicates: list[dict[str, str]] = []
    for name, address, symbol_type, bind in entries:
        if not name or symbol_type not in SYMBOL_TYPES or address == 0:
            continue
        if name not in symbols:
            symbols[name] = (address, bind)
            continue
        current_address, current_bind = symbols[name]
        duplicates.append({"name": name, "addr": hex(address)})
        if bind in GLOBAL_BINDS and current_bind not in GLOBAL_BINDS:
            symbols[name] = (address, bind)
            duplicates.append({"name": name, "addr": hex(current_address)})
    return {name: hex(address) for name, (address, _) in symbols.items()}, duplicates


def parse_symbols(path):
    """Parse an ELF shared object and return ``(symbols, duplicates)``."""
    with open(path, "rb") as handle:
        elf = ELFFile(handle)
        section = elf.get_section_by_name(".symtab") or elf.get_section_by_name(".dynsym")
        if section is None:
            raise ValueError(f"no symbol table in {path}")
        entries = (
            (
                symbol.name,
                symbol["st_value"],
                symbol["st_info"]["type"],
                symbol["st_info"]["bind"],
            )
            for symbol in section.iter_symbols()
            if symbol["st_shndx"] != "SHN_UNDEF"
        )
        return collect_symbols(entries)


def build_document(*, version, revision, lib_path, sym_path, symbols, duplicates):
    return {
        "unity_version": version,
        "short_revision": revision,
        "abi": ABI,
        "variant": VARIANT,
        "lib_sha256": _sha256(lib_path),
        "sym_sha256": _sha256(sym_path),
        "symbol_count": len(symbols),
        "symbols": symbols,
        "duplicate_symbols": duplicates,
    }
