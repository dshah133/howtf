"""Resolve an executable's dynamic dependency closure.

Walks DT_NEEDED breadth-first, honoring DT_RPATH / DT_RUNPATH (with $ORIGIN)
and an explicit --ld-library-path, in the order the dynamic linker uses. This
is a static model of what ld.so would load -- it does NOT execute anything.

Ordering (glibc, simplified but faithful for the split question):
  1. DT_RPATH of the loading object   (deprecated; only if no DT_RUNPATH)
  2. LD_LIBRARY_PATH / --ld-library-path
  3. DT_RUNPATH of the loading object
  4. default system dirs (/lib, /usr/lib, multiarch)
The executable is always first in the resulting global scope list.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

from .elffacts import parse_module
from .model import ModuleFacts

_DEFAULT_DIRS = [
    "/lib", "/usr/lib",
    "/lib64", "/usr/lib64",
    "/lib/aarch64-linux-gnu", "/usr/lib/aarch64-linux-gnu",
    "/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu",
]


def _expand(paths: List[str], origin: str) -> List[str]:
    out = []
    for p in paths:
        if not p:
            continue
        p = p.replace("$ORIGIN", origin).replace("${ORIGIN}", origin)
        out.append(os.path.normpath(p))
    return out


def _find(soname: str, search_dirs: List[str]) -> Optional[str]:
    for d in search_dirs:
        cand = os.path.join(d, soname)
        if os.path.isfile(cand):
            return os.path.realpath(cand)
    return None


class Closure:
    def __init__(self, modules: List[ModuleFacts], missing: List[str]):
        self.modules = modules            # global-scope order: exe first
        self.missing = missing            # sonames we could not resolve

    @property
    def exe(self) -> Optional[ModuleFacts]:
        for m in self.modules:
            if m.is_exe:
                return m
        return self.modules[0] if self.modules else None


def resolve_closure(exe_path: str,
                    ld_library_path: Optional[List[str]] = None,
                    extra_modules: Optional[List[str]] = None,
                    rtld_global: bool = False) -> Closure:
    ld_library_path = ld_library_path or []
    extra_modules = extra_modules or []

    exe_path = os.path.realpath(exe_path)
    exe = parse_module(exe_path, is_exe=True)

    modules: List[ModuleFacts] = [exe]
    seen_paths = {exe_path}
    seen_soname = {exe.soname} if exe.soname else set()

    # BFS over DT_NEEDED
    queue: List[Tuple[ModuleFacts, str]] = [(exe, n) for n in exe.needed]
    missing: List[str] = []
    while queue:
        loader, soname = queue.pop(0)
        if soname in seen_soname:
            continue
        origin = os.path.dirname(loader.path)
        search = []
        if not loader.runpath:                       # RPATH only if no RUNPATH
            search += _expand(loader.rpath, origin)
        search += ld_library_path
        search += _expand(loader.runpath, origin)
        search += _DEFAULT_DIRS
        found = _find(soname, search)
        if not found or found in seen_paths:
            if not found:
                missing.append(soname)
            seen_soname.add(soname)
            continue
        mod = parse_module(found)
        modules.append(mod)
        seen_paths.add(found)
        seen_soname.add(soname)
        if mod.soname:
            seen_soname.add(mod.soname)
        queue += [(mod, n) for n in mod.needed]

    # dlopen-style explicit modules (RTLD_LOCAL by default)
    for mp in extra_modules:
        rp = os.path.realpath(mp)
        if rp in seen_paths:
            continue
        mod = parse_module(rp, is_dlopened=True, rtld_global=rtld_global)
        modules.append(mod)
        seen_paths.add(rp)
        # also pull the dlopened module's own NEEDED into the image
        queue = [(mod, n) for n in mod.needed]
        while queue:
            loader, soname = queue.pop(0)
            if soname in seen_soname:
                continue
            origin = os.path.dirname(loader.path)
            search = []
            if not loader.runpath:
                search += _expand(loader.rpath, origin)
            search += ld_library_path
            search += _expand(loader.runpath, origin)
            search += _DEFAULT_DIRS
            found = _find(soname, search)
            seen_soname.add(soname)
            if not found or found in seen_paths:
                if not found:
                    missing.append(soname)
                continue
            dep = parse_module(found)
            modules.append(dep)
            seen_paths.add(found)
            if dep.soname:
                seen_soname.add(dep.soname)
            queue += [(dep, n) for n in dep.needed]

    return Closure(modules, missing)
