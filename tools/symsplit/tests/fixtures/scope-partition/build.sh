#!/usr/bin/env bash
# Route B (SCOPE-PARTITION) + clustering (--by-library) fixture.
#
# Two auditwheel-style vendored copies of the SAME tiny library
# (libworkshim-<hash>.so.1), built from identical source with NO special
# link flags (no -Bsymbolic anywhere -- Route B needs no self-binding at
# all). Composed via --module in the tests, so each defaults to its own
# isolated local (RTLD_LOCAL) scope -- neither is in the shared/global
# scope, so the two copies of wq_get_state / wq_set_state / wq_add can never
# unify -> SCOPE-PARTITION for each, and --by-library should collapse all
# three into ONE "copies=2 shared_symbols=3" cluster row instead of three
# separate per-symbol rows.
#
# libother.so exports an unrelated symbol under a different soname prefix,
# to prove the clustering fingerprint does NOT merge unrelated libraries
# together just because they were both passed via --module.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/src" && pwd)"
OUT="${1:-$(cd "$(dirname "$0")" && pwd)/build}"
CC=${CC:-gcc}
FP="-O0 -g -fPIC"
F="-O0 -g"
rm -rf "$OUT"; mkdir -p "$OUT"

( cd "$OUT"
  $CC $FP -shared -Wl,-soname,libworkshim-aaaa1111.so.1 \
     "$SRC/workshim.c" -o libworkshim-aaaa1111.so.1
  $CC $FP -shared -Wl,-soname,libworkshim-bbbb2222.so.1 \
     "$SRC/workshim.c" -o libworkshim-bbbb2222.so.1
  printf 'int other_probe(void){return 42;}\n' > _other.c
  $CC $FP -shared -Wl,-soname,libother.so.1 _other.c -o libother.so.1
  printf 'int main(void){return 0;}\n' > _m.c
  $CC $F _m.c -o app_scope

  # app_scope_linked: the SAME two vendored copies, but as ORDINARY
  # DT_NEEDED links (not --module) -- the default model treats this as one
  # shared/global scope (no split, nothing references them anyway), while
  # --assume-rtld-local isolates them per-module for the scope-partition
  # check even though they are plainly linked.
  printf 'int main(void){return 0;}\n' > _m2.c
  $CC $F _m2.c -o app_scope_linked -L. -Wl,-rpath,'$ORIGIN' \
     -Wl,--no-as-needed -l:libworkshim-aaaa1111.so.1 -l:libworkshim-bbbb2222.so.1
  rm -f _other.c _m.c _m2.c )

echo "built scope-partition fixture into $OUT"
