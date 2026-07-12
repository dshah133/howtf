#!/usr/bin/env bash
# Static analysis: per-wheel load groups + multi-wheel upper-bound unions.
# Depends only on downloaded wheels in /scratch/wheels.
set -u
OUT=/work/demo/rdma-symbol-collision/survey/full/results/static
mkdir -p "$OUT"
W=/scratch/wheels
export PYTHONPATH=/work/tools/symsplit
DRV="python3 /work/demo/rdma-symbol-collision/survey/full/static_driver.py"

echo "### per-wheel static units $(date -u +%FT%TZ)"
for whl in "$W"/*.whl; do
  [ -f "$whl" ] || continue
  name=$(basename "$whl" | sed 's/-.*//')
  echo "--- wheel: $name"
  $DRV "wheel_$name" "$OUT" --wheels "$whl" 2>&1 | tail -2
done

echo "### multi-wheel static UNION units (upper bound, over-models scope)"
pick() { ls "$W"/$1-*.whl 2>/dev/null | head -1; }
declare -A UNIONS=(
  [union_torch_faiss]="torch faiss_cpu"
  [union_faiss_sklearn_torch]="faiss_cpu scikit_learn torch"
  [union_numpy_scipy]="numpy scipy"
  [union_torch_numpy_scipy]="torch numpy scipy"
)
for u in "${!UNIONS[@]}"; do
  whls=""
  for pk in ${UNIONS[$u]}; do w=$(pick "$pk"); [ -n "$w" ] && whls="$whls $w"; done
  [ -n "$whls" ] || { echo "SKIP $u (missing wheel)"; continue; }
  echo "--- union: $u [$whls]"
  $DRV "$u" "$OUT" --wheels $whls 2>&1 | tail -2
done
echo "### static done $(date -u +%FT%TZ)"
