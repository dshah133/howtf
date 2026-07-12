#!/usr/bin/env bash
# Build ONE shared venv for the co-load captures. Each co-load unit is a fresh
# `python -c "import ..."` process against this venv, so the RTLD scope is
# per-process and a shared venv is faithful. Versions pinned to match the
# statically-extracted wheels. torch comes from the CPU-only index.
set -u
VENV=/scratch/venvs/mega
echo "### building co-load venv $(date -u +%FT%TZ)"
python3 -m venv "$VENV"
VPY="$VENV/bin/python"
"$VPY" -m pip install -q --upgrade pip >/dev/null 2>&1

echo "--- torch (cpu index)"
"$VPY" -m pip install -q --no-input torch==2.4.1 \
  --index-url https://download.pytorch.org/whl/cpu 2>&1 | tail -3 \
  || echo "torch install FAILED"

echo "--- numpy/scipy/faiss/onnxruntime/scikit-learn (PyPI)"
"$VPY" -m pip install -q --no-input \
  numpy==2.0.2 scipy==1.14.1 faiss-cpu==1.8.0.post1 \
  onnxruntime==1.19.2 scikit-learn==1.5.2 2>&1 | tail -5 \
  || echo "sci-stack install FAILED"

echo "### pip freeze:"
"$VPY" -m pip freeze | grep -iE "torch|numpy|scipy|faiss|onnx|scikit|joblib|threadpool|sympy|networkx" 2>/dev/null

echo "### import smoke test (each in its own process):"
for m in numpy scipy torch faiss "sklearn" onnxruntime; do
  "$VPY" -c "import $m; print('OK  $m', getattr($m,'__version__','?'))" 2>&1 | tail -1 \
    || echo "IMPORT-FAIL $m"
done
echo "### venv build done $(date -u +%FT%TZ)"
