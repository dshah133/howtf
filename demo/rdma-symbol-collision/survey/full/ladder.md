## Co-load units (REALISTIC /proc/maps images) — the tier ladder

| unit | imports | modules | Tier0 | Tier1 | Tier2 | verdicts |
|---|---|---|---|---|---|---|
| pair_numpy_scipy | numpy,scipy | 25 | 272 | 2 | **0** | HIDDEN-BENIGN=2 NO-SPLIT=272 WEAK-PATTERN=18 |
| pair_torch_faiss | torch,faiss | 39 | 6225 | 5 | **0** | ALLOWLISTED=5 HIDDEN-BENIGN=930 NO-SPLIT=7671 WEAK-PATTERN=1803 |
| pair_torch_onnx | torch,onnxruntime | 37 | 708 | 5 | **0** | ALLOWLISTED=5 HIDDEN-BENIGN=909 NO-SPLIT=703 WEAK-PATTERN=1752 |
| set_faiss_sklearn_torch | faiss,sklearn,torch | 132 | 11974 | 5 | **0** | ALLOWLISTED=5 HIDDEN-BENIGN=1174 NO-SPLIT=11969 WEAK-PATTERN=1803 |
| set_torch_numpy_scipy | torch,numpy,scipy | 36 | 708 | 5 | **0** | ALLOWLISTED=5 HIDDEN-BENIGN=909 NO-SPLIT=703 WEAK-PATTERN=1752 |
| single_faiss | faiss | 30 | 5566 | 2 | **0** | ALLOWLISTED=4 HIDDEN-BENIGN=2 NO-SPLIT=7013 WEAK-PATTERN=269 |
| single_numpy | numpy | 24 | 272 | 2 | **0** | HIDDEN-BENIGN=2 NO-SPLIT=272 WEAK-PATTERN=18 |
| single_torch | torch | 35 | 708 | 5 | **0** | ALLOWLISTED=5 HIDDEN-BENIGN=909 NO-SPLIT=703 WEAK-PATTERN=1752 |

**HEADLINE: 0 of 8 co-load units contain >=1 predicted SPLIT.**

### Dangerous-family reach across co-load units
| family | Tier0 symbols (dup, strong) | Tier2 symbols (SPLIT) |
|---|---|---|
| BLAS/LAPACK | 2453 | 0 |
| Fortran-RT | 1279 | 0 |
| OpenMP | 244 | 0 |
| libstdc++ | 264 | 0 |

## Static units (per-wheel load groups + multi-wheel UNION upper bound)

| unit | wheels | sos | modules | Tier0 | Tier1 | Tier2 |
|---|---|---|---|---|---|---|
| union_faiss_sklearn_torch | faiss_cpu-1.8.0.post1-cp311-cp311-manyli | 74 | 84 | 8690 | 4 | **0** |
| union_numpy_scipy | numpy-2.0.2-cp311-cp311-manylinux_2_17_x | 140 | 149 | 11303 | 2 | **0** |
| union_torch_faiss | faiss_cpu-1.8.0.post1-cp311-cp311-manyli | 6 | 16 | 8603 | 0 | **0** |
| union_torch_numpy_scipy | numpy-2.0.2-cp311-cp311-manylinux_2_17_x | 140 | 149 | 11303 | 2 | **0** |
| wheel_cupy_cuda12x | cupy_cuda12x-13.3.0-cp311-cp311-manylinu | 65 | 75 | 108 | 3 | **0** |
| wheel_faiss_cpu | faiss_cpu-1.8.0.post1-cp311-cp311-manyli | 6 | 16 | 8603 | 0 | **0** |
| wheel_grpcio | grpcio-1.66.1-cp311-cp311-manylinux_2_17 | 1 | 10 | 109 | 0 | **0** |
| wheel_lightgbm | lightgbm-4.5.0-py3-none-manylinux_2_28_x | 1 | 11 | 13 | 0 | **0** |
| wheel_numpy | numpy-2.0.2-cp311-cp311-manylinux_2_17_x | 22 | 31 | 370 | 2 | **0** |
| wheel_onnxruntime | onnxruntime-1.19.2-cp311-cp311-manylinux | 3 | 13 | 10 | 0 | **0** |
| wheel_opencv_python_headless | opencv_python_headless-4.10.0.84-cp37-ab | 13 | 24 | 15 | 5 | **0** |
| wheel_pillow | pillow-10.4.0-cp311-cp311-manylinux_2_17 | 23 | 30 | 17 | 5 | **0** |
| wheel_protobuf | protobuf-5.28.2-cp38-abi3-manylinux2014_ | 1 | 8 | 10 | 0 | **0** |
| wheel_pyarrow | pyarrow-17.0.0-cp311-cp311-manylinux_2_1 | 30 | 40 | 122 | 2 | **0** |
| wheel_safetensors | safetensors-0.4.5-cp311-cp311-manylinux_ | 1 | 10 | 10 | 0 | **0** |
| wheel_scikit_learn | scikit_learn-1.5.2-cp311-cp311-manylinux | 68 | 76 | 105 | 4 | **0** |
| wheel_scipy | scipy-1.14.1-cp311-cp311-manylinux_2_17_ | 118 | 127 | 1463 | 2 | **0** |
| wheel_sentencepiece | sentencepiece-0.2.0-cp311-cp311-manylinu | 1 | 9 | 121 | 111 | **0** |
| wheel_xgboost | xgboost-2.1.1-py3-none-manylinux2014_x86 | 2 | 11 | 412 | 0 | **0** |

## All Tier-2 SPLIT findings (for adjudication)

_None. Tier-2 is empty across every unit._

**Total Tier-2 SPLIT findings across all units: 0**