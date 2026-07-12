# Pilot: split-state latent-hazard precondition in real ML wheels

Wheels analyzed: 8  |  total .so files: 290

## Per-wheel

| wheel | #.so | #strong-global C syms | #-Bsymbolic .so |
|---|---|---|---|
| faiss_cpu | 6 | 17954 | 0 |
| numpy | 22 | 12987 | 0 |
| onnxruntime | 3 | 7 | 0 |
| pillow | 26 | 3922 | 0 |
| pyarrow | 37 | 7605 | 0 |
| scikit_learn | 70 | 525 | 0 |
| scipy | 114 | 12894 | 0 |
| torch | 12 | 27952 | 0 |

## Cross-wheel duplicate strong global C symbols (the precondition)

Total distinct strong-global C symbols defined in >=2 different wheels: **8181**

### By dangerous family

| family | # cross-wheel dup symbols | example symbols (wheels) |
|---|---|---|
| OpenMP | 281 | `GOMP_1.0` (faiss_cpu+scikit_learn+torch); `GOMP_2.0` (faiss_cpu+scikit_learn+torch); `GOMP_3.0` (faiss_cpu+scikit_learn+torch) |
| BLAS/LAPACK | 130 | `cblas_daxpy` (faiss_cpu+torch); `cblas_saxpy` (faiss_cpu+torch); `cblas_xerbla` (faiss_cpu+torch) |
| Fortran RT | 1279 | `_gfortran_abort` (faiss_cpu+numpy+scipy); `_gfortran_access_func` (faiss_cpu+numpy+scipy); `_gfortran_adjustl` (faiss_cpu+numpy+scipy) |
| libstdc++ | 1 | `__cxa_call_terminate` (faiss_cpu+pyarrow) |

### Top cross-wheel duplicate symbols by breadth (most wheels sharing)

| symbol | #wheels | wheels |
|---|---|---|
| `GFORTRAN_8` | 3 | faiss_cpu, numpy, scipy |
| `GFORTRAN_C99_8` | 3 | faiss_cpu, numpy, scipy |
| `GFORTRAN_F2C_8` | 3 | faiss_cpu, numpy, scipy |
| `GOACC_2.0` | 3 | faiss_cpu, scikit_learn, torch |
| `GOACC_2.0.1` | 3 | faiss_cpu, scikit_learn, torch |
| `GOACC_data_end` | 3 | faiss_cpu, scikit_learn, torch |
| `GOACC_data_start` | 3 | faiss_cpu, scikit_learn, torch |
| `GOACC_declare` | 3 | faiss_cpu, scikit_learn, torch |
| `GOACC_enter_exit_data` | 3 | faiss_cpu, scikit_learn, torch |
| `GOACC_get_num_threads` | 3 | faiss_cpu, scikit_learn, torch |
| `GOACC_get_thread_num` | 3 | faiss_cpu, scikit_learn, torch |
| `GOACC_parallel` | 3 | faiss_cpu, scikit_learn, torch |
| `GOACC_parallel_keyed` | 3 | faiss_cpu, scikit_learn, torch |
| `GOACC_update` | 3 | faiss_cpu, scikit_learn, torch |
| `GOACC_wait` | 3 | faiss_cpu, scikit_learn, torch |
| `GOMP_1.0` | 3 | faiss_cpu, scikit_learn, torch |
| `GOMP_2.0` | 3 | faiss_cpu, scikit_learn, torch |
| `GOMP_3.0` | 3 | faiss_cpu, scikit_learn, torch |
| `GOMP_4.0` | 3 | faiss_cpu, scikit_learn, torch |
| `GOMP_4.0.1` | 3 | faiss_cpu, scikit_learn, torch |

## Bundled duplicate copies of the SAME library

| lib family | #wheels | wheels |
|---|---|---|
| libgomp | 3 | faiss_cpu, scikit_learn, torch |
| libquadmath | 2 | faiss_cpu, scipy |
| libgfortran | 2 | faiss_cpu, scipy |
| libgfortran-040039e | 2 | numpy, scipy |
| libquadmath-96973f | 2 | numpy, scipy |

## -Bsymbolic .so's (the trigger)

Total .so's built with DF_SYMBOLIC: **0**


## VERDICT

**Preconditions rampant; trigger status unknown pending relocation-level analysis.** (Reframed from an earlier "STRONG" label — the 8,181 figure is an nm-level Tier-0 count that ignores scope and would be shredded by the "dupes ≠ bugs" objection. It appears downstream only as Tier 0, with these caveats attached.)

Two important nuances this nm-based pilot **cannot** resolve — both are why the full survey uses the `symsplit` binding simulator and direct `/proc/maps` observation instead of nm counts:

1. **The Route-A trigger is invisible here.** `-Bsymbolic-functions` (the self-binding trigger from the gating experiment) sets **no ELF flag** — so "0 of 290 .so's set DF_SYMBOLIC" tells you nothing about it. Only relocation-level analysis (does the .so retain an interposable JUMP_SLOT/GLOB_DAT to its own exported symbol?) can assess it.
2. **These duplicates are two ROUTES, not one.** Route A (interposition capture — the incident's shape) needs the self-binding trigger, likely absent from manylinux wheels (it lives in monorepo static-link land you can't scan from PyPI). Route B (scope partition) needs only RTLD_LOCAL + vendored duplication — and the libgomp×3 / libgfortran×2 bundled copies are a **confirmed Route-B precondition**, measurable directly by counting mapped copies in one Python process. The ecosystem already knows Route B as the "multiple OpenMP runtimes" problem (Intel's `KMP_DUPLICATE_LIB_OK`).

The full survey (`survey/full/`) resolves both via `symsplit` + `/proc/maps` copy-counting.

