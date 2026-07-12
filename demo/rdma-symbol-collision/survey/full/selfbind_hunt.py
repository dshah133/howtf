"""Ecosystem-wide hunt for the split TRIGGER.

A split is only possible if some defining DSO self-binds its own copy of a
duplicated symbol (DF_SYMBOLIC, or the -Bsymbolic-functions signature: no
interposable JUMP_SLOT/GLOB_DAT to any own export). This scans EVERY .so in
every extracted wheel and reports each module's self-binding status, so we can
say honestly how many split-capable modules exist in the ecosystem at all.

Reports, per .so:
  status  = confirmed | self-bound-or-unreferenced | interposable
  n_exports         = # global-default dynsym defs (real interposable API)
  n_self_interposable = # own exports still named by a JUMP_SLOT/GLOB_DAT
A module is "split-capable" iff status != interposable AND it exports real API.
"""
import json
import os
import sys

sys.path.insert(0, "/work/tools/symsplit")
from symsplit.elffacts import parse_module          # noqa: E402
from symsplit.model import SelfBind                  # noqa: E402

ROOT = "/scratch/extracted"
LINKER = {"_init", "_fini", "__bss_start", "_edata", "_end",
          "__data_start", "__dso_handle", "_IO_stdin_used"}


def is_elf(p):
    try:
        with open(p, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def real_exports(m):
    return [n for n, sd in m.defs.items()
            if sd.in_dynsym and sd.bind == "GLOBAL"
            and sd.visibility == "DEFAULT" and n not in LINKER]


def main():
    rows = []
    capable = []
    for dirpath, _d, files in os.walk(ROOT):
        for fn in files:
            p = os.path.join(dirpath, fn)
            base = os.path.basename(p)
            if not (base.endswith(".so") or ".so." in base):
                continue
            if not is_elf(p):
                continue
            try:
                m = parse_module(p)
            except Exception as e:                   # noqa: BLE001
                rows.append({"so": base, "error": str(e)})
                continue
            status = m.selfbind_status()
            exps = real_exports(m)
            # split-capable = self-binding AND exports real API AND that API is
            # not fully covered by linker-only names
            is_capable = (status != SelfBind.NOT_SELF_BOUND and len(exps) > 0)
            wheel = dirpath.replace(ROOT + "/", "").split("/")[0]
            row = {
                "wheel": wheel, "so": base, "status": status,
                "n_exports": len(exps), "n_self_interposable": len(m.self_interposable),
                "df_symbolic": m.df_symbolic, "split_capable": is_capable,
                "sample_exports": sorted(exps)[:5],
            }
            rows.append(row)
            if is_capable:
                capable.append(row)

    total = len([r for r in rows if "status" in r])
    by_status = {}
    for r in rows:
        if "status" in r:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    out = {
        "n_so_scanned": total,
        "status_counts": by_status,
        "n_split_capable": len(capable),
        "split_capable_modules": capable,
        "all_rows": rows,
    }
    json.dump(out, open("/work/demo/rdma-symbol-collision/survey/full/results/selfbind_hunt.json", "w"), indent=2)
    print("scanned %d .so | status: %s" % (total, by_status))
    print("SPLIT-CAPABLE modules (status!=interposable AND real exports): %d" % len(capable))
    for r in capable:
        print("  [%s] %s status=%s exports=%d self_interposable=%d df_symbolic=%s sample=%s"
              % (r["wheel"], r["so"], r["status"], r["n_exports"],
                 r["n_self_interposable"], r["df_symbolic"], r["sample_exports"]))


if __name__ == "__main__":
    main()
