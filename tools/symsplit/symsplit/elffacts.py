"""Extract the ELF facts symsplit keys on, from a single module.

Everything here is read-only ELF parsing via pyelftools. The parser is
architecture-agnostic: relocations are matched by the *name suffix* of their
type (JUMP_SLOT / GLOB_DAT / COPY), never a numeric constant, so x86-64,
aarch64, etc. all work.
"""
from __future__ import annotations

from typing import Dict, Optional

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection
from elftools.elf.dynamic import DynamicSection
from elftools.elf.gnuversions import (
    GNUVerSymSection,
    GNUVerDefSection,
    GNUVerNeedSection,
)

from .model import ModuleFacts, SymDef

# st_other visibility field (low 2 bits)
_VIS = {0: "DEFAULT", 1: "INTERNAL", 2: "HIDDEN", 3: "PROTECTED"}


def _bind(sym) -> str:
    return sym.entry.st_info.bind.replace("STB_", "")


def _type(sym) -> str:
    return sym.entry.st_info.type.replace("STT_", "")


def _vis(sym) -> str:
    v = sym.entry.st_other.visibility  # pyelftools gives e.g. 'STV_DEFAULT'
    if isinstance(v, str):
        return v.replace("STV_", "")
    return _VIS.get(v & 0x3, "DEFAULT")


def _is_undef(sym) -> bool:
    return sym.entry.st_shndx == "SHN_UNDEF"


class _VersionIndex:
    """Maps a .dynsym index -> defined-version name (or None)."""

    def __init__(self, elf: ELFFile):
        self._ndx_to_name: Dict[int, str] = {}
        self._versym = None
        self.version_node_names = set()   # verdef node names (pseudo-symbols)
        versym = elf.get_section_by_name(".gnu.version")
        verdef = elf.get_section_by_name(".gnu.version_d")
        verneed = elf.get_section_by_name(".gnu.version_r")
        if isinstance(versym, GNUVerSymSection):
            self._versym = versym
        # verdef: version *definitions* provided by this module
        if isinstance(verdef, GNUVerDefSection):
            try:
                for verdef_entry, aux_iter in verdef.iter_versions():
                    aux = list(aux_iter)
                    if aux:
                        self._ndx_to_name[verdef_entry.entry.vd_ndx] = aux[0].name
                        for a in aux:
                            self.version_node_names.add(a.name)
            except Exception:
                pass
        # verneed: versions this module *requires* from others (for undef refs)
        if isinstance(verneed, GNUVerNeedSection):
            try:
                for _verneed, aux_iter in verneed.iter_versions():
                    for aux in aux_iter:
                        self._ndx_to_name[aux.entry.vna_other] = aux.name
            except Exception:
                pass

    def defined_version(self, dynsym_index: int) -> Optional[str]:
        if self._versym is None:
            return None
        try:
            raw = self._versym.get_symbol(dynsym_index).entry.ndx
        except Exception:
            return None
        # pyelftools yields 'VER_NDX_LOCAL'/'VER_NDX_GLOBAL' for 0/1 (unversioned)
        if not isinstance(raw, int):
            return None
        # 0 = local, 1 = global/base (unversioned); high bit 0x8000 = hidden
        idx = raw & 0x7FFF
        if idx <= 1:
            return None
        return self._ndx_to_name.get(idx)


def _reloc_symbol_names(elf: ELFFile, dynsym) -> "tuple[set, set, set]":
    """Return (jump_slot_names, glob_dat_names, copy_names) referenced by
    dynamic relocations. Names come from the .dynsym via r_info_sym."""
    from elftools.elf.descriptions import describe_reloc_type

    jump, glob, copy = set(), set(), set()
    for secname in (".rela.plt", ".rel.plt", ".rela.dyn", ".rel.dyn"):
        sec = elf.get_section_by_name(secname)
        if sec is None:
            continue
        try:
            relocs = sec.iter_relocations()
        except Exception:
            continue
        for r in relocs:
            symidx = r["r_info_sym"]
            if symidx == 0 or dynsym is None:
                continue
            try:
                name = dynsym.get_symbol(symidx).name
            except Exception:
                continue
            if not name:
                continue
            rtype = describe_reloc_type(r["r_info_type"], elf)
            if rtype.endswith("JUMP_SLOT") or rtype.endswith("JMP_SLOT"):
                jump.add(name)
            elif rtype.endswith("GLOB_DAT"):
                glob.add(name)
            elif rtype.endswith("COPY"):
                copy.add(name)
    return jump, glob, copy


def parse_module(path: str, is_exe: bool = False,
                 is_dlopened: bool = False, rtld_global: bool = False) -> ModuleFacts:
    with open(path, "rb") as f:
        elf = ELFFile(f)
        etype = elf["e_type"]
        dynsym = elf.get_section_by_name(".dynsym")
        symtab = elf.get_section_by_name(".symtab")

        m = ModuleFacts(
            path=path,
            name=path.rsplit("/", 1)[-1],
            soname=None,
            is_exe=is_exe or etype == "ET_EXEC",
            is_dlopened=is_dlopened,
            rtld_global=rtld_global,
        )

        # --- dynamic segment: NEEDED / RPATH / RUNPATH / FLAGS / SONAME ------
        dyn = elf.get_section_by_name(".dynamic")
        if isinstance(dyn, DynamicSection):
            for tag in dyn.iter_tags():
                t = tag.entry.d_tag
                if t == "DT_NEEDED":
                    m.needed.append(tag.needed)
                elif t == "DT_SONAME":
                    m.soname = tag.soname
                    m.name = tag.soname
                elif t == "DT_RPATH":
                    m.rpath.extend(str(tag.rpath).split(":"))
                elif t == "DT_RUNPATH":
                    m.runpath.extend(str(tag.runpath).split(":"))
                elif t == "DT_FLAGS":
                    if tag.entry.d_val & 0x2:   # DF_SYMBOLIC
                        m.df_symbolic = True
                elif t in ("DT_INIT", "DT_INIT_ARRAY"):
                    m.has_init = True
                elif t == "DT_INIT_ARRAYSZ" and tag.entry.d_val > 0:
                    m.has_init = True

        verindex = _VersionIndex(elf)

        # --- .dynsym: exported defs + undef references -----------------------
        dynsym_names_defined = set()
        if isinstance(dynsym, SymbolTableSection):
            for i, sym in enumerate(dynsym.iter_symbols()):
                name = sym.name
                if not name:
                    continue
                b = _bind(sym)
                if _is_undef(sym):
                    if b in ("GLOBAL", "WEAK"):
                        m.undefs.add(name)
                    continue
                # skip version-node pseudo-symbols (STT_OBJECT, ABS, name is a
                # verdef node like GLIBC_2.17) -- not real API, never split
                if (sym.entry.st_shndx == "SHN_ABS"
                        and name in verindex.version_node_names):
                    continue
                sd = SymDef(
                    name=name, bind=b, visibility=_vis(sym), type=_type(sym),
                    size=sym.entry.st_size, version=verindex.defined_version(i),
                    in_dynsym=True, in_symtab=False,
                )
                m.defs[name] = sd
                dynsym_names_defined.add(name)

        # --- .symtab: pick up defs that are NOT in .dynsym --------------------
        # We DO record local/hidden symtab-only defs: they are "shadow" copies
        # that cannot interpose, but their existence is what distinguishes a
        # HIDDEN-BENIGN / NOT-DYNAMIC-BENIGN dup from a real one. A shadow copy
        # only ever produces a finding when a participating copy of the SAME
        # name exists in another module, so this does not add output noise.
        if isinstance(symtab, SymbolTableSection):
            for sym in symtab.iter_symbols():
                name = sym.name
                if not name or _is_undef(sym):
                    continue
                if sym.entry.st_shndx == "SHN_ABS" and name in verindex.version_node_names:
                    continue
                if name in m.defs:
                    m.defs[name].in_symtab = True
                    continue
                m.defs[name] = SymDef(
                    name=name, bind=_bind(sym), visibility=_vis(sym),
                    type=_type(sym), size=sym.entry.st_size, version=None,
                    in_dynsym=False, in_symtab=True,
                )

        # --- relocations: self-binding + copy-reloc signals ------------------
        jump, glob, copy = _reloc_symbol_names(elf, dynsym)
        m.copy_relocs = copy
        # A self-interposable reloc = an interposable reloc naming one of THIS
        # module's own defined global-default symbols. Its presence proves the
        # module kept an interposable reference to its own export -> not
        # globally self-bound.
        for name in (jump | glob):
            sd = m.defs.get(name)
            if sd and sd.in_dynsym and sd.bind == "GLOBAL" and sd.visibility == "DEFAULT":
                m.self_interposable.add(name)

        return m
