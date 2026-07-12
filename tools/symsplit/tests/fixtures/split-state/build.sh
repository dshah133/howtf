#!/usr/bin/env bash
# Build the four canonical split-state configurations as real ELF binaries.
#
# Sources are vendored verbatim from
#   demo/rdma-symbol-collision/local/split-state/src/
# (the ground-truth reproducer). Only the LINK COMPOSITION differs per config;
# app.c/verbs.c/collective*.c are identical everywhere.
#
# Each config gets its own output directory containing the executable plus the
# exact DSO closure it loads, so symsplit can be pointed at `<dir>/<exe>` and
# resolve its closure locally ($ORIGIN rpath). Config expectations:
#
#   A   (default)                 -> NOT a split (exe copy interposes; DSO ctor
#                                     binds to it too, unifying the two copies)
#   B   (-Bsymbolic-functions)    -> SPLIT (DSO ctor self-binds to DSO copy;
#                                     collective reads the exe copy)
#   D2  (copy-relocation, data)   -> NOT a split (global table in exe+DSO is
#                                     unified by a copy relocation)
#   Ch  (hidden + --as-needed)    -> NOT a split (DSO dropped from DT_NEEDED;
#                                     only one copy is ever loaded)
#
# Run inside a Linux/ELF toolchain (see the container in the test harness).
set -euo pipefail
SRC="$(cd "$(dirname "$0")/src" && pwd)"
OUT="${1:-$(cd "$(dirname "$0")" && pwd)/build}"
CC=${CC:-gcc}
CF="-O0 -g"
CFPIC="-O0 -g -fPIC"
DSHARED="-DVX_ORIGIN='\"SHARED\"' -DVX_WITH_CTOR"
DSTATIC="-DVX_ORIGIN='\"STATIC\"'"
RPATH="-Wl,-rpath,'\$ORIGIN'"

rm -rf "$OUT"; mkdir -p "$OUT"

build_one() {
  local dir="$OUT/$1"; mkdir -p "$dir"; shift
  ( cd "$dir" && eval "$@" )
}

# ---- shared inputs rebuilt per-config-dir (libverbs_shared.so varies) -------

# (A) default: DSO exports verbs fns, no special flags -> NO split.
build_one A "
  $CC $CFPIC -shared $SRC/collective.c -o libcollective.so
  $CC $CFPIC $DSHARED -shared $SRC/verbs.c -o libverbs_shared.so
  $CC $CF $DSTATIC -c $SRC/verbs.c -o verbs_static.o && ar crs libverbs_static.a verbs_static.o
  $CC $CF $SRC/app.c -o app_A -L. $RPATH -rdynamic -lcollective -lverbs_shared \
     -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive
  rm -f verbs_static.o libverbs_static.a"

# (B) -Bsymbolic-functions: DSO self-binds its ctor -> SPLIT.
build_one B "
  $CC $CFPIC -shared $SRC/collective.c -o libcollective.so
  $CC $CFPIC $DSHARED -Wl,-Bsymbolic-functions -shared $SRC/verbs.c -o libverbs_shared.so
  $CC $CF $DSTATIC -c $SRC/verbs.c -o verbs_static.o && ar crs libverbs_static.a verbs_static.o
  $CC $CF $SRC/app.c -o app_B -L. $RPATH -rdynamic -lcollective -lverbs_shared \
     -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive
  rm -f verbs_static.o libverbs_static.a"

# (D2) global table in exe+DSO: copy relocation unifies -> NO split.
build_one D2 "
  $CC $CFPIC -shared $SRC/collective_data.c -o libcollective_data.so
  $CC $CFPIC $DSHARED -DVX_TABLE_GLOBAL -Wl,-Bsymbolic-functions -shared $SRC/verbs.c -o libverbs_shared.so
  $CC $CF $DSTATIC -DVX_TABLE_GLOBAL -c $SRC/verbs.c -o verbs_static_g.o && ar crs libverbs_static_g.a verbs_static_g.o
  $CC $CF $SRC/app.c -o app_D2 -L. $RPATH -rdynamic -lcollective_data \
     -Wl,--no-as-needed -lverbs_shared -Wl,--as-needed \
     -Wl,--whole-archive -lverbs_static_g -Wl,--no-whole-archive
  rm -f verbs_static_g.o libverbs_static_g.a"

# (Ch) hidden DSO symbols + --as-needed: DSO dropped from DT_NEEDED -> NO split.
build_one Ch "
  $CC $CFPIC -shared $SRC/collective.c -o libcollective.so
  $CC $CFPIC $DSHARED -DVX_VIS_HIDDEN -shared $SRC/verbs.c -o libverbs_shared.so
  $CC $CF $DSTATIC -c $SRC/verbs.c -o verbs_static.o && ar crs libverbs_static.a verbs_static.o
  $CC $CF $SRC/app.c -o app_Ch -L. $RPATH -rdynamic -lcollective \
     -Wl,--as-needed -lverbs_shared \
     -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive
  rm -f verbs_static.o libverbs_static.a"

echo "built configs into $OUT"
