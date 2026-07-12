#!/usr/bin/env bash
# Capture the split-state forensic chain into ./artifacts/ inside the container.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-howtf-rdma-lab:latest}"
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "toolchain image $IMAGE missing; building it..." >&2
  "$HERE/build-image.sh"
fi
exec docker run --rm -v "$HERE":/lab -w /lab "$IMAGE" bash evidence.sh
