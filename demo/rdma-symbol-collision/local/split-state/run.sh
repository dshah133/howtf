#!/usr/bin/env bash
# Run one or more Makefile targets inside the Linux/GNU toolchain container.
# Usage:  ./run.sh [target ...]   (default: all)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-howtf-rdma-lab:latest}"
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "toolchain image $IMAGE missing; building it..." >&2
  "$HERE/build-image.sh"
fi
exec docker run --rm -v "$HERE":/lab -w /lab "$IMAGE" make ${@:-all}
