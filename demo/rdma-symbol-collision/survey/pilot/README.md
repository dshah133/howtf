# Pilot: does the split-state hazard precondition exist in real ML wheels?

Quick-and-dirty pilot (the polished scanner comes later) to decide whether an
**ecosystem-survey framing is load-bearing** for the post. It measures the
*precondition* for the split-state / symbol-collision hazard in real wheels, not
"bugs" — duplicate strong symbols are common and mostly harmless; the question
is whether the dangerous *shape* actually occurs.

## What it measures (precondition, not bugs)

For a set of popular manylinux wheels, across the `.so`s that would coexist if
the wheels are **co-imported into one Python process** (the real risk: `import
torch, faiss, onnxruntime`):

1. **Duplicate strong global C symbols** — symbols that are strong (not weak),
   global, and DEFINED in two or more `.so`s from *different* wheels. C-linkage
   only; C++ mangled `_Z...` names are skipped for the pilot (the C case is the
   hazard we care about). This is the precondition.
2. **`-Bsymbolic` / `-Bsymbolic-functions` `.so`s** (`readelf -d` → `DF_SYMBOLIC`)
   — the actual *trigger*: per the gating experiment, a duplicated strong symbol
   only silently splits when a defining `.so` self-binds. A duplicate + a
   `-Bsymbolic` `.so` is a genuine latent hazard.
3. **Bundled duplicate copies of the SAME library** (two `libgomp` / `libopenblas`
   / `libprotobuf` / `libstdc++` across wheels) — the classic vendored-duplicate
   shape.

## How to run

Runs inside a `linux/amd64` Python container so the downloaded manylinux wheels
(x86_64 ELF) are analyzed natively with GNU binutils (`nm`, `readelf`):

```sh
docker run --rm --platform linux/amd64 -v "$PWD":/out -w /out python:3.11 \
    bash /out/run-pilot.sh
```

Wheels are `pip download --no-deps --only-binary=:all:` (download only, never
installed/executed) into the container's `/tmp` (NOT committed). Only the
analysis lands in this dir. Default wheel set (override with `WHEELS=`):
`numpy scipy faiss-cpu onnxruntime pyarrow scikit-learn torch pillow`.

## Outputs (committed)

- `report.md` — per-wheel table, cross-wheel duplicate symbol families, bundled
  duplicate libraries, `-Bsymbolic` `.so`s, and the STRONG / THIN / NULL verdict.
- `raw_cross_wheel_dupes.tsv` — every strong-global C symbol defined in >=2
  wheels, with the wheel list.
- `raw_symbolic_sos.tsv` — the `.so`s built `-Bsymbolic`.
- `raw_bundled_dup_libs.tsv` — library families bundled by >=2 wheels.

## Interpretation of the pilot run (analyst notes)

Verdict: **STRONG** signal for the *precondition*, with one honest caveat on the
*trigger*.

- **Precondition is pervasive.** Across 8 wheels (290 `.so`s), thousands of
  strong-global C symbols are defined in 2+ co-importable wheels. Filtering out
  the ~22 symbol-version node names (`GOMP_1.0`, `GFORTRAN_8` — metadata, not
  functions), the substance is real duplicated runtime functions:
  ~1279 `_gfortran_*`, ~284 OpenMP/OpenACC (`GOACC_parallel`, `omp_*`), and BLAS
  (`cblas_*`).
- **Ground truth: duplicated runtime libraries.** `libgomp` ships in 3 wheels
  (faiss-cpu, scikit-learn, torch); `libgfortran` and `libquadmath` in multiple
  (numpy, scipy, faiss). `cblas_*` appears in both faiss's bundled OpenBLAS and
  torch's `libtorch_cpu`. `import torch, faiss, sklearn` loads three OpenMP
  runtimes into one process — the classic hazard shape.
- **Trigger caveat (honest).** **Zero** `.so`s in this sample are `-Bsymbolic`
  (`DF_SYMBOLIC`). So the *silent split-state* variant specifically (constructor
  writes one copy, discovery reads another) needs its trigger from elsewhere —
  protected visibility (not measured per-symbol here) or the static-archive-order
  mechanism. The broader duplicate-strong-symbol interposition hazard (one copy
  silently wins for everyone) is present regardless of `-Bsymbolic`.

Net: an ecosystem-survey framing is **load-bearing** for "duplicate strong C
symbols across co-imported ML wheels are pervasive, including dangerous runtime
families." The full survey should additionally scan for protected-visibility
symbols and widen the wheel set to hunt for `-Bsymbolic` defining `.so`s.

## Honesty notes / limits of the pilot

- Duplicate strong symbols are a **precondition**, not a bug. A real silent split
  additionally needs the co-import to happen, a self-binding (`-Bsymbolic`)
  defining `.so`, and diverging behavior between the copies.
- C++ mangled symbols are skipped, so protobuf/absl/onnx C++ ABIs are
  under-counted here; the C-linkage families (OpenMP, BLAS/LAPACK, Fortran
  runtime) are the ones surfaced.
- Wheel versions are whatever `pip` resolved on the run date; see `report.md`
  header for the exact set. This is a signal test, not a census.
