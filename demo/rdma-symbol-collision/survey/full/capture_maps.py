"""Import a set of modules, then dump the REAL loaded .so set from /proc/self/maps.

Run inside the target venv's python. This is the ground-truth dlopen graph +
scope for a co-load unit: no guessing which extensions get pulled in.

Usage: python capture_maps.py <out.json> <mod1> [mod2 ...]
Writes JSON: {imported, failed, sos: [abs paths], py_exe, sys_prefix}
"""
import importlib
import json
import os
import sys


def loaded_sos() -> list:
    sos = set()
    with open("/proc/self/maps") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 6:
                continue
            path = parts[-1]
            if not path.startswith("/"):
                continue
            base = os.path.basename(path)
            if base.endswith(".so") or ".so." in base:
                if os.path.isfile(path):
                    sos.add(os.path.realpath(path))
    return sorted(sos)


def main() -> int:
    out = sys.argv[1]
    mods = sys.argv[2:]
    imported, failed = [], {}
    for m in mods:
        try:
            importlib.import_module(m)
            imported.append(m)
        except Exception as e:                       # noqa: BLE001
            failed[m] = f"{type(e).__name__}: {e}"
    data = {
        "imported": imported,
        "failed": failed,
        "sos": loaded_sos(),
        "py_exe": os.path.realpath(sys.executable),
        "sys_prefix": sys.prefix,
    }
    with open(out, "w") as f:
        json.dump(data, f, indent=2)
    print("imported=%s failed=%s sos=%d" % (imported, list(failed), len(data["sos"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
