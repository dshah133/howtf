#!/usr/bin/env bash
# Build the reusable GNU/ELF toolchain image the lab runs in.
#
# We provision + commit by hand instead of `docker build` because this machine
# routes `docker build` through a depot driver that needs a project id. Plain
# `docker run` + `docker commit` sidesteps that and is fully reproducible.
set -euo pipefail
IMAGE="${IMAGE:-howtf-rdma-lab:latest}"

if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "image $IMAGE already exists; nothing to do"
  exit 0
fi

docker rm -f howtf-rdma-provision >/dev/null 2>&1 || true
docker run --name howtf-rdma-provision ubuntu:24.04 bash -c \
  'apt-get update && apt-get install -y --no-install-recommends build-essential binutils make ca-certificates && rm -rf /var/lib/apt/lists/*'
docker commit howtf-rdma-provision "$IMAGE"
docker rm howtf-rdma-provision
echo "built $IMAGE"
