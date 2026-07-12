"""ROUTE A — interposition capture via scope promotion (the reproducible hit).

Route A needs a duplicate + an interposing definition in a SHARED (global)
scope. Public wheels dlopen extensions RTLD_LOCAL, so normally no wheel is in
another's scope -- EXCEPT a wheel that deliberately promotes its libs with
RTLD_GLOBAL. torch does exactly that: torch/__init__.py loads
libtorch_global_deps.so with ctypes.CDLL(..., mode=RTLD_GLOBAL), and that lib
DT_NEEDEDs libgomp -> torch's OpenMP runtime lands in the global scope.

Controlled experiment (this file): exercise faiss's OpenMP parallel regions
under LD_DEBUG and record which libgomp its extension binds to, in two worlds:
  (1) faiss alone         -> expect faiss's OWN bundled libgomp (isolated),
  (2) import torch first  -> expect torch's libgomp (captured by promotion).
A flip between (1) and (2) is the Route-A hit: torch's RTLD_GLOBAL promotion
interposes faiss's OpenMP references onto torch's copy.

Usage: python routeA_scope_promotion.py <out.json>
"""
import json
import os
import re
import subprocess
import sys

VPY = "/scratch/venvs/mega/bin/python"
SP = "/scratch/venvs/mega/lib/python3.11/site-packages"
BIND_RE = re.compile(r"binding file\s+(?P<ref>\S+)\s+\[\d+\]\s+to\s+(?P<def>\S+)\s+\[\d+\]:"
                     r"\s+normal symbol\s+`(?P<sym>[^']+)'")
WORKLOAD_TAIL = (
    "import numpy as np, faiss\n"
    "idx=faiss.IndexFlatL2(64); idx.add(np.random.rand(400,64).astype('float32'))\n"
    "idx.search(np.random.rand(20,64).astype('float32'),8)\n")
OMP = ("GOMP_parallel", "omp_get_num_threads", "omp_get_max_threads",
       "GOMP_barrier", "omp_in_parallel")


def probe(preamble):
    for fn in os.listdir("/tmp"):
        if fn.startswith("ra."):
            os.remove(os.path.join("/tmp", fn))
    env = {**os.environ, "LD_DEBUG": "bindings", "LD_DEBUG_OUTPUT": "/tmp/ra"}
    subprocess.run([VPY, "-c", preamble + WORKLOAD_TAIL],
                   capture_output=True, text=True, env=env)
    blob = ""
    for fn in os.listdir("/tmp"):
        if fn.startswith("ra."):
            blob += open(os.path.join("/tmp", fn)).read()
    # count, for faiss's _swigfaiss extension, which libgomp file it binds OMP to
    targets = {}
    for line in blob.splitlines():
        m = BIND_RE.search(line)
        if not m or m.group("sym") not in OMP:
            continue
        if "_swigfaiss" not in m.group("ref"):
            continue
        d = m.group("def").replace(SP + "/", "")
        if "libgomp" in d:
            targets[d] = targets.get(d, 0) + 1
    return targets


def main():
    out = sys.argv[1]
    alone = probe("")
    withtorch = probe("import torch\n_=torch.rand(8,8)@torch.rand(8,8)\n")
    result = {
        "experiment": "faiss OpenMP binding target vs torch scope promotion",
        "faiss_alone__swigfaiss_binds_libgomp": alone,
        "torch_first__swigfaiss_binds_libgomp": withtorch,
        "route_A_hit": (set(alone) != set(withtorch)) and bool(alone) and bool(withtorch),
        "mechanism": "torch/__init__.py ctypes.CDLL(libtorch_global_deps.so, RTLD_GLOBAL); "
                     "that lib DT_NEEDEDs libgomp -> torch libgomp enters global scope and "
                     "interposes faiss's OpenMP references.",
    }
    json.dump(result, open(out, "w"), indent=2)
    print(json.dumps(result, indent=2))
    print("\nROUTE-A HIT:", result["route_A_hit"])


if __name__ == "__main__":
    main()
