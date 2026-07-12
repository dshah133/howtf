"""Tier-3 (workload): exercise real BLAS/OMP compute across co-loaded libs under
LD_DEBUG, then confirm every reference to a duplicated hot symbol bound to a
SINGLE definition (runtime unification), i.e. no split-state divergence.

Unlike import-only capture, this forces lazy PLT binding of the compute symbols
(gemm, omp_*) by actually calling into torch, numpy, and faiss.

Usage: python tier3_workload.py <venv_python> <out.json>
"""
import json
import os
import re
import subprocess
import sys

BIND_RE = re.compile(
    r"binding file\s+(?P<ref>\S+)\s+\[\d+\]\s+to\s+(?P<def>\S+)\s+\[\d+\]:"
    r"\s+normal symbol\s+`(?P<sym>[^']+)'")

WORKLOAD = r"""
import numpy as np
a = np.random.rand(64, 64); b = np.random.rand(64, 64)
_ = a @ b                                   # numpy BLAS (gemm) + threads
import torch
x = torch.rand(64, 64); y = torch.rand(64, 64)
_ = x @ y                                   # torch BLAS/OMP
_ = torch.nn.functional.relu(x)
import faiss
idx = faiss.IndexFlatL2(64)
idx.add(np.random.rand(200, 64).astype('float32'))
_D, _I = idx.search(np.random.rand(10, 64).astype('float32'), 5)  # faiss BLAS/OMP
print('workload-ok')
"""

HOT_PREFIXES = ("omp_", "GOMP_", "GOACC_", "cblas_", "sgemm", "dgemm",
                "openblas", "gomp")


def is_hot(sym: str) -> bool:
    return sym.startswith(HOT_PREFIXES) or "gemm" in sym.lower()


def main() -> int:
    vpy, out = sys.argv[1], sys.argv[2]
    env = {**os.environ, "LD_DEBUG": "bindings", "LD_DEBUG_OUTPUT": "/tmp/w"}
    r = subprocess.run([vpy, "-c", WORKLOAD], capture_output=True, text=True, env=env)
    blob = r.stderr
    for fn in os.listdir("/tmp"):
        if fn.startswith("w."):
            try:
                blob += open(os.path.join("/tmp", fn)).read()
                os.remove(os.path.join("/tmp", fn))
            except OSError:
                pass

    per_sym = {}
    for line in blob.splitlines():
        m = BIND_RE.search(line)
        if not m:
            continue
        sym = m.group("sym")
        if not is_hot(sym):
            continue
        d = os.path.basename(m.group("def"))
        ref = os.path.basename(m.group("ref"))
        per_sym.setdefault(sym, {}).setdefault(d, set()).add(ref)

    result = {}
    for sym, defs in sorted(per_sym.items()):
        result[sym] = {
            "distinct_defs": sorted(defs),
            "n_refs": sum(len(v) for v in defs.values()),
            "split_confirmed": len(defs) >= 2,
            "bindings": {d: sorted(refs) for d, refs in defs.items()},
        }
    out_obj = {
        "workload_rc": r.returncode,
        "workload_ok": "workload-ok" in r.stdout,
        "n_hot_symbols_bound": len(result),
        "any_split_confirmed": any(v["split_confirmed"] for v in result.values()),
        "per_symbol": result,
    }
    json.dump(out_obj, open(out, "w"), indent=2)
    print("workload_ok=%s hot_symbols_bound=%d any_split_confirmed=%s"
          % (out_obj["workload_ok"], len(result), out_obj["any_split_confirmed"]))
    for sym, v in result.items():
        print("  %-22s defs=%s refs=%d split=%s"
              % (sym, v["distinct_defs"], v["n_refs"], v["split_confirmed"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
