#!/bin/bash
# regenerate.sh — rebuilds the demo and re-captures every artifact the post
# shows, all inside the ONE canonical environment (ubuntu:22.04, linux/amd64).
# Run from this directory on the host. Artifacts land in artifacts/.
#
# Note for Apple Silicon hosts: containers run under Rosetta emulation.
# /proc/<pid>/maps captures are filtered to the rows the post discusses
# (app, libmath, libc, ld-linux, stack); the post discloses the emulation.
set -eu
cd "$(dirname "$0")"

echo '== [1/2] canonical container: build + capture (ubuntu:22.04 amd64) =='
docker run --rm --platform=linux/amd64 \
  --cap-add=SYS_PTRACE --security-opt seccomp=unconfined \
  -v "$PWD":/code -w /code ubuntu:22.04 bash -c '
    set -e
    apt-get update -qq >/dev/null
    DEBIAN_FRONTEND=noninteractive apt-get install -yqq build-essential binutils gdb >/dev/null
    bash /code/capture.sh
  '

echo '== [2/2] error #1: GLIBC_2.34 not found (build on 24.04, run on 20.04) =='
docker run --rm --platform=linux/amd64 -v "$PWD":/code -w /code ubuntu:24.04 bash -c '
    set -e
    apt-get update -qq >/dev/null
    DEBIAN_FRONTEND=noninteractive apt-get install -yqq build-essential binutils >/dev/null
    gcc -shared -fPIC -o libmath-24.so math.c
    gcc -o dynamic_app_glibc234 main.c -L. -l:libmath-24.so -Wl,-rpath,"\$ORIGIN"
    readelf -V dynamic_app_glibc234 | sed -n "/.gnu.version_r/,\$p" > artifacts/glibc234-verneed.txt
    ldd --version | head -1 > artifacts/glibc234-build-glibc.txt
  '
docker run --rm --platform=linux/amd64 -v "$PWD":/code -w /code ubuntu:20.04 bash -c '
    { ldd --version | head -1
      echo "# running the 24.04-built binary on 20.04:"
      ./dynamic_app_glibc234 2>&1 || true
    } > artifacts/glibc234-error.txt 2>&1
  '
rm -f dynamic_app_glibc234 libmath-24.so
echo 'done. artifacts/:'
ls artifacts/
