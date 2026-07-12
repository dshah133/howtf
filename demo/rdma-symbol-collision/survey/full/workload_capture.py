"""Import + EXERCISE a composition, then dump the loaded .so set from
/proc/self/maps. Exercising forces lazy-loaded runtimes (libgfortran via LAPACK,
libgomp via parallel regions) to actually map, so the Route-B copy count is the
true resident set, not the import-only lower bound.

Usage: python workload_capture.py <out_maps.json> <tag>
  tag selects a workload: trio | numpy_scipy | torch_onnx | tv_ta
"""
import json
import os
import sys


def dump(out):
    sos = set()
    for ln in open("/proc/self/maps"):
        p = ln.split()[-1]
        if p.startswith("/") and ".so" in os.path.basename(p) and os.path.isfile(p):
            sos.add(os.path.realpath(p))
    json.dump({"sos": sorted(sos)}, open(out, "w"))
    print("mapped .so:", len(sos))


def main():
    out, tag = sys.argv[1], sys.argv[2]
    import numpy as np
    a = np.random.rand(96, 96)
    _ = a @ a
    _ = np.linalg.svd(a)                       # numpy LAPACK

    if tag in ("trio", "numpy_scipy"):
        import scipy.linalg as sla
        _ = sla.lu(a)                          # scipy LAPACK -> libgfortran
        _ = sla.qr(a)
        import scipy.fft as sfft
        _ = sfft.fft(a[0])

    if tag in ("trio", "torch_onnx", "tv_ta"):
        import torch
        x = torch.rand(96, 96)
        _ = x @ x
        _ = torch.linalg.svd(x)
        _ = torch.nn.functional.conv2d(torch.rand(1, 3, 32, 32), torch.rand(4, 3, 3, 3))

    if tag == "trio":
        import faiss
        idx = faiss.IndexFlatL2(96)
        idx.add(np.random.rand(500, 96).astype("float32"))
        _ = idx.search(np.random.rand(20, 96).astype("float32"), 8)
        import sklearn.cluster as skc
        _ = skc.KMeans(n_clusters=4, n_init=2).fit(np.random.rand(200, 96))

    if tag == "torch_onnx":
        import onnxruntime  # noqa: F401

    if tag == "tv_ta":
        import torchvision, torchaudio     # noqa: F401
        _ = torchvision.ops.nms(torch.rand(10, 4), torch.rand(10), 0.5)

    dump(out)


if __name__ == "__main__":
    main()
