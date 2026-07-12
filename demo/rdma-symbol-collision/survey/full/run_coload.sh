#!/usr/bin/env bash
# Co-load analysis: real process images captured from /proc/self/maps against
# the shared mega venv. Each unit is a distinct import subset -> distinct
# realistic dlopen graph + RTLD scope.
set -u
OUT=/work/demo/rdma-symbol-collision/survey/full/results/coload
mkdir -p "$OUT"
VENV=/scratch/venvs/mega
export PYTHONPATH=/work/tools/symsplit
DRV="python3 /work/demo/rdma-symbol-collision/survey/full/coload_driver.py"

run() { # name  "import1 import2 ..."
  local name="$1"; shift
  echo "--- coload: $name  (import: $*)"
  $DRV "$name" "$OUT" --reuse-venv "$VENV" --pkgs _reuse_ --imports "$@" 2>&1 | tail -3
}

echo "### co-load units $(date -u +%FT%TZ)"
# singles (baseline: python+libpython vs one wheel's bundled libs)
run single_numpy numpy
run single_torch torch
run single_faiss faiss
# the task's pairs / sets
run pair_torch_faiss torch faiss
run pair_torch_onnx torch onnxruntime
run set_faiss_sklearn_torch faiss sklearn torch
run pair_numpy_scipy numpy scipy
run set_torch_numpy_scipy torch numpy scipy
echo "### co-load done $(date -u +%FT%TZ)"
