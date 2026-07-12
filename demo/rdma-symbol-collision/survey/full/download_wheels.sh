#!/usr/bin/env bash
# Download pinned manylinux x86_64 cp311 wheels for STATIC extraction.
# Records the exact wheel filenames actually fetched (versions are in the name).
# Runs inside the x86_64 container. torch uses the CPU-only index.
set -u
DEST=/scratch/wheels
mkdir -p "$DEST"
PLATS=(--platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 \
       --platform manylinux_2_28_x86_64 --platform manylinux_2_35_x86_64)
COMMON=(--no-deps --only-binary=:all: --python-version 311 --implementation cp --abi cp311 -d "$DEST")

# pkg==version, pinned. torch handled separately (CPU index).
PKGS=(
  "numpy==2.0.2"
  "scipy==1.14.1"
  "faiss-cpu==1.8.0.post1"
  "onnxruntime==1.19.2"
  "scikit-learn==1.5.2"
  "pyarrow==17.0.0"
  "pillow==10.4.0"
  "xgboost==2.1.1"
  "lightgbm==4.5.0"
  "opencv-python-headless==4.10.0.84"
  "grpcio==1.66.1"
  "protobuf==5.28.2"
  "sentencepiece==0.2.0"
  "safetensors==0.4.5"
  "cupy-cuda12x==13.3.0"
)

echo "### static-extraction downloads $(date -u +%FT%TZ)"
for spec in "${PKGS[@]}"; do
  echo "--- $spec"
  pip download "${COMMON[@]}" "${PLATS[@]}" "$spec" >/dev/null 2>"$DEST/.err.$$" \
    && echo "OK $spec" \
    || { echo "PIN-FAIL $spec -> retry unpinned latest"; \
         base="${spec%%==*}"; \
         pip download "${COMMON[@]}" "${PLATS[@]}" "$base" >/dev/null 2>"$DEST/.err.$$" \
           && echo "OK (unpinned) $base" || { echo "SKIP $base"; tail -2 "$DEST/.err.$$"; }; }
done

echo "--- torch==2.4.1 (CPU index)"
pip download "${COMMON[@]}" "${PLATS[@]}" --index-url https://download.pytorch.org/whl/cpu \
  "torch==2.4.1" >/dev/null 2>"$DEST/.err.$$" \
  && echo "OK torch==2.4.1+cpu" || { echo "torch pin failed, trying 2.2.2"; \
     pip download "${COMMON[@]}" "${PLATS[@]}" --index-url https://download.pytorch.org/whl/cpu \
       "torch==2.2.2" >/dev/null 2>"$DEST/.err.$$" && echo "OK torch==2.2.2+cpu" || echo "SKIP torch"; }

rm -f "$DEST/.err.$$"
echo "### downloaded wheels:"
ls -la "$DEST"/*.whl 2>/dev/null | awk '{print $5, $9}'
echo "### total: $(ls "$DEST"/*.whl 2>/dev/null | wc -l) wheels"
