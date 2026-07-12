#!/usr/bin/env bash
# Run a Makefile target inside the Linux/GNU toolchain container.
# Usage:  ./run.sh [target]     (default target: all)
# Examples:
#   ./run.sh collision
#   ./run.sh explicit-error
#   ./run.sh              # runs every variant
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-howtf-rdma-lab:latest}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "toolchain image $IMAGE missing; building it..." >&2
  "$HERE/build-image.sh"
fi

exec docker run --rm -v "$HERE":/lab -w /lab "$IMAGE" make "${@:-all}"
