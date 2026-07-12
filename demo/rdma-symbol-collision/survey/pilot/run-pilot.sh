#!/usr/bin/env bash
# Pilot wheel-scan. Runs INSIDE a linux/amd64 python container so the downloaded
# manylinux wheels (x86_64 ELF) can be analyzed natively with GNU binutils.
#
# Launch from the host:
#   docker run --rm --platform linux/amd64 \
#     -v "$PWD":/out -w /out python:3.11 bash /out/run-pilot.sh
#
# Wheels download to /tmp (container-local, NOT committed); only report.md and
# the raw_*.tsv analysis land in /out (this repo dir).
set -uo pipefail
# Default index wheels (bundle their own vendored .so's via auditwheel *.libs/).
WHEELS="${WHEELS:-numpy scipy faiss-cpu onnxruntime pyarrow scikit-learn pillow}"
# torch is pulled from the CPU index so we get the ~180MB CPU wheel, not the
# multi-GB CUDA-bundling default wheel (which stalls the pilot under emulation).
TORCH_CPU_INDEX="https://download.pytorch.org/whl/cpu"
PER_WHEEL_TIMEOUT="${PER_WHEEL_TIMEOUT:-300}"

apt-get -qq update >/dev/null 2>&1
apt-get -qq install -y binutils unzip coreutils >/dev/null 2>&1
mkdir -p /tmp/wheels /tmp/extract

for w in $WHEELS; do
  echo ">> pip download $w"
  timeout "$PER_WHEEL_TIMEOUT" pip download --no-deps --only-binary=:all: -d /tmp/wheels "$w" >/tmp/dl_${w}.log 2>&1 \
    || echo "   FAILED/timeout $w (see /tmp/dl_${w}.log)"
done

echo ">> pip download torch (CPU index)"
timeout "$PER_WHEEL_TIMEOUT" pip download --no-deps --only-binary=:all: --index-url "$TORCH_CPU_INDEX" -d /tmp/wheels torch >/tmp/dl_torch.log 2>&1 \
  || echo "   FAILED/timeout torch (see /tmp/dl_torch.log)"

echo ">> wheels obtained:"
ls -lh /tmp/wheels/*.whl 2>/dev/null | awk '{print "  ", $5, $9}'

for whl in /tmp/wheels/*.whl; do
  n=$(basename "$whl" .whl)
  mkdir -p "/tmp/extract/$n"
  unzip -q -o "$whl" -d "/tmp/extract/$n" || true
done

python3 /out/analyze.py
