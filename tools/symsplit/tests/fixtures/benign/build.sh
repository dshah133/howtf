#!/usr/bin/env bash
# Five hand-built benign fixtures that share the "duplicate symbol" shape but
# must each return a specific NON-split verdict. This is what proves symsplit
# is a binding simulator, not a duplicate lister.
#
#   weak       -> WEAK-PATTERN        (global + weak override idiom)
#   versioned  -> VERSIONED-BENIGN    (two defs under different version nodes)
#   hidden     -> HIDDEN-BENIGN       (a copy has non-default visibility)
#   allowlist  -> ALLOWLISTED         (malloc: interposition is the intent)
#   symtab     -> NOT-DYNAMIC-BENIGN  (extra copy lives only in .symtab)
set -euo pipefail
SRC="$(cd "$(dirname "$0")/src" && pwd)"
OUT="${1:-$(cd "$(dirname "$0")" && pwd)/build}"
CC=${CC:-gcc}
F="-O0 -g"; FP="-O0 -g -fPIC"
RP="-Wl,-rpath,\$ORIGIN"   # literal $ORIGIN (not re-expanded without eval)
rm -rf "$OUT"

# 1) WEAK: exe strong plugin_hook, DSO weak default, consumer calls it.
d="$OUT/weak"; mkdir -p "$d"; ( cd "$d"
  $CC $FP -shared "$SRC/weak.c"          -o libprovider.so
  $CC $FP -shared "$SRC/consumer_hook.c" -o libconsumer.so -L. -lprovider
  $CC $F  "$SRC/strong.c" -c -o strong.o; ar crs libstrong.a strong.o
  printf 'void use_hook(void);int main(void){use_hook();return 0;}\n' > _m.c
  $CC $F _m.c -o app_weak -L. $RP -rdynamic -lconsumer \
     -Wl,--whole-archive -lstrong -Wl,--no-whole-archive
  rm -f strong.o libstrong.a _m.c )

# 2) VERSIONED: two DSOs export api_call under different version nodes.
d="$OUT/versioned"; mkdir -p "$d"; ( cd "$d"
  $CC $FP -shared "$SRC/verv.c" -Wl,--version-script="$SRC/v1.map" -o libv1.so
  $CC $FP -shared "$SRC/verv.c" -Wl,--version-script="$SRC/v2.map" -o libv2.so
  $CC $FP -shared "$SRC/consumer_api.c" -o libconsumer.so -L. -lv1
  printf 'void use_api(void);int main(void){use_api();return 0;}\n' > _m.c
  $CC $F _m.c -o app_versioned -L. $RP -rdynamic \
     -Wl,--no-as-needed -lv1 -lv2 -Wl,--as-needed -lconsumer
  rm -f _m.c )

# 3) HIDDEN: exe exports helper (default); DSO has a private hidden helper.
d="$OUT/hidden"; mkdir -p "$d"; ( cd "$d"
  $CC $FP -shared "$SRC/hidden_provider.c" -o libprovider.so
  $CC $F "$SRC/helper_exe.c" -o app_hidden -L. $RP -rdynamic \
     -Wl,--no-as-needed -lprovider -Wl,--as-needed )

# 4) ALLOWLISTED: DSO custom malloc (self-bound), exe static malloc, consumer.
d="$OUT/allowlist"; mkdir -p "$d"; ( cd "$d"
  $CC $FP -Wl,-Bsymbolic-functions -shared "$SRC/alloc.c" -o liballoc.so
  $CC $FP -shared "$SRC/consumer_malloc.c" -o libconsumer.so -L. -lalloc
  $CC $F "$SRC/alloc_static.c" -c -o as.o; ar crs liballoc_static.a as.o
  printf 'void use_malloc(void);int main(void){use_malloc();return 0;}\n' > _m.c
  $CC $F _m.c -o app_allowlist -L. $RP -rdynamic \
     -Wl,--no-as-needed -lalloc -Wl,--as-needed -lconsumer \
     -Wl,--whole-archive -lalloc_static -Wl,--no-whole-archive
  rm -f as.o liballoc_static.a _m.c )

# 5) NOT-DYNAMIC: exe's dupfn() is a GLOBAL symbol that stays in .symtab only
#    (not -rdynamic, not colliding at link time). The provider (dlopen-style,
#    passed to symsplit via --module) exports dupfn in .dynsym. The exe copy
#    is global but not dynamic -> cannot interpose -> no dynamic split.
d="$OUT/symtab"; mkdir -p "$d"; ( cd "$d"
  $CC $FP -shared "$SRC/secret_provider.c" -o libprovider.so
  $CC $F "$SRC/secret_exe.c" -o app_symtab )

echo "built benign fixtures into $OUT"
