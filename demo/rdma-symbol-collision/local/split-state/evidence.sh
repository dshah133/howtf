#!/usr/bin/env bash
# evidence.sh - capture the split-state forensic chain into ./artifacts/.
# Run inside the toolchain container (see evidence-in-docker.sh). Every artifact
# is real command output.
set -uo pipefail
ART=artifacts
B=build
mkdir -p "$ART"
rm -f "$ART"/*.txt 2>/dev/null || true
CFPIC="-O0 -g -fPIC"; CF="-O0 -g"
RUN="LD_LIBRARY_PATH=$B"

log(){ echo "=== $* ==="; }

log "00 toolchain"
{ uname -a; gcc --version|head -1; ld --version|head -1; objcopy --version|head -1; } > "$ART/00_toolchain.txt"

make clean >/dev/null 2>&1; mkdir -p "$B"

# ---- build the bug-a composition ----
gcc $CFPIC -shared src/collective.c -o "$B/libcollective.so"
gcc $CF -DVX_ORIGIN='"STATIC"' -c src/verbs.c -o "$B/verbs_static.o"; ar crs "$B/libverbs_static.a" "$B/verbs_static.o"
gcc $CFPIC -DVX_ORIGIN='"SHARED"' -DVX_WITH_CTOR -Wl,-Bsymbolic -shared src/verbs.c -o "$B/libverbs_shared.so"
gcc $CF src/app.c -o "$B/app_bug_a" -L"$B" -rdynamic -lcollective -lverbs_shared \
    -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive

log "01 nm: same vx_* symbols defined in BOTH the executable and the DSO"
{
  echo "# executable app_bug_a (T = defined global, exported via -rdynamic):"
  nm "$B/app_bug_a" | grep -E "vx_get_device_list|vx_register_device"
  echo
  echo "# libverbs_shared.so (its own copy of the same symbols):"
  nm -D "$B/libverbs_shared.so" | grep -E "vx_get_device_list|vx_register_device"
} > "$ART/01_nm_duplicate_symbols.txt"

log "02 runtime trace (which copy registered vs was read)"
eval "$RUN ./$B/app_bug_a" > "$ART/02_bug_a_run.txt" 2>&1

log "03 LD_DEBUG=bindings,symbols: which copy each reference bound to"
rm -f /tmp/ld.* 2>/dev/null || true
eval "LD_DEBUG=bindings,symbols LD_DEBUG_OUTPUT=/tmp/ld $RUN ./$B/app_bug_a" >/dev/null 2>&1 || true
{
  echo "# The collective's vx_get_device_list reference binds to the EXECUTABLE"
  echo "# (app_bug_a), i.e. the empty static copy -- while the constructor filled"
  echo "# the SHARED copy. That split is the bug."
  echo
  cat /tmp/ld.* 2>/dev/null | grep -iE "vx_register_device|vx_get_device_list" \
    | grep -i "binding" | sed -E 's#/lab/##g' | sort -u
} > "$ART/03_ld_debug_bindings.txt"

log "04 bug-b (inverse)"
gcc $CF -DVX_ORIGIN='"STATIC"' -DVX_WITH_CTOR -c src/verbs.c -o "$B/verbs_static_ctor.o"; ar crs "$B/libverbs_static_ctor.a" "$B/verbs_static_ctor.o"
gcc $CFPIC -DVX_ORIGIN='"SHARED"' -shared src/verbs.c -o "$B/libverbs_shared_noctor.so"
gcc $CF src/app.c -o "$B/app_bug_b" -L"$B" -lcollective -lverbs_shared_noctor \
    -Wl,--whole-archive -lverbs_static_ctor -Wl,--no-whole-archive -Wl,--exclude-libs,ALL
eval "$RUN ./$B/app_bug_b" > "$ART/04_bug_b_run.txt" 2>&1

log "05 nondeterminism (identical sources, one link token apart)"
gcc $CFPIC -DVX_ORIGIN='"SHARED"' -DVX_WITH_CTOR -Wl,-Bsymbolic -shared src/verbs.c -o "$B/libverbs_shared.so"
gcc $CF src/app.c -o "$B/app_with_static"    -L"$B" -rdynamic -lcollective -lverbs_shared -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive
gcc $CF src/app.c -o "$B/app_without_static" -L"$B" -rdynamic -lcollective -lverbs_shared
{
  echo "# app.c and both .so's are byte-identical; only the app link line differs."
  echo "app_with_static    (redundant static copy linked):  $(eval "$RUN ./$B/app_with_static"    2>/dev/null | grep discovered)"
  echo "app_without_static (single copy):                    $(eval "$RUN ./$B/app_without_static" 2>/dev/null | grep discovered)"
} > "$ART/05_nondeterminism.txt"

log "06 fix ladder + honest negatives"
run_variant(){ eval "$RUN ./$B/$1" 2>/dev/null | grep discovered; }
{
  echo "FIXES THAT WORK:"
  # drop-duplicate
  gcc $CF src/app.c -o "$B/app_fix_single" -L"$B" -rdynamic -lcollective -lverbs_shared
  echo "  fix-drop-duplicate : $(run_variant app_fix_single)"
  # exclude-libs
  gcc $CF src/app.c -o "$B/app_fix_excl" -L"$B" -rdynamic -lcollective -lverbs_shared -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive -Wl,--exclude-libs,ALL
  echo "  fix-exclude-libs   : $(run_variant app_fix_excl)"
  # prefix-rename
  gcc $CFPIC -DVX_ORIGIN='"SHARED"' -DVX_WITH_CTOR -shared src/verbs.c -o "$B/libverbs_shared_raw.so"
  objcopy --redefine-sym vx_get_device_list=prov_vx_get_device_list --redefine-sym vx_register_device=prov_vx_register_device "$B/libverbs_shared_raw.so" "$B/libverbs_shared_ns.so"
  gcc $CFPIC -shared src/collective.c -o "$B/libcollective_raw.so"
  objcopy --redefine-sym vx_get_device_list=prov_vx_get_device_list "$B/libcollective_raw.so" "$B/libcollective_ns.so"
  gcc $CF src/app.c -o "$B/app_fix_ns" -L"$B" -rdynamic -l:libcollective_ns.so -l:libverbs_shared_ns.so -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive
  echo "  fix-prefix-rename  : $(run_variant app_fix_ns)"
  echo
  echo "NAIVE FIXES THAT DO NOT WORK (they hide the wrong copy):"
  gcc $CFPIC -fvisibility=hidden -DVX_ORIGIN='"SHARED"' -DVX_WITH_CTOR -shared src/verbs.c -o "$B/libverbs_shared.so"
  gcc $CF src/app.c -o "$B/app_nofix_vis" -L"$B" -rdynamic -lcollective -lverbs_shared -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive
  echo "  nofix-visibility     (-fvisibility=hidden on DSO): $(run_variant app_nofix_vis)"
  gcc $CFPIC -DVX_ORIGIN='"SHARED"' -DVX_WITH_CTOR -Wl,--version-script=src/hide-dso.map -shared src/verbs.c -o "$B/libverbs_shared.so"
  gcc $CF src/app.c -o "$B/app_nofix_vs" -L"$B" -rdynamic -lcollective -lverbs_shared -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive
  echo "  nofix-version-script (local:* on DSO):             $(run_variant app_nofix_vs)"
  echo
  echo "TRIGGER (not a fix): -Bsymbolic on the DSO turns a working binary broken:"
  gcc $CFPIC -DVX_ORIGIN='"SHARED"' -DVX_WITH_CTOR -shared src/verbs.c -o "$B/libverbs_shared.so"
  gcc $CF src/app.c -o "$B/app_nobsym" -L"$B" -rdynamic -lcollective -lverbs_shared -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive
  echo "  DSO without -Bsymbolic: $(run_variant app_nobsym)"
  gcc $CFPIC -DVX_ORIGIN='"SHARED"' -DVX_WITH_CTOR -Wl,-Bsymbolic -shared src/verbs.c -o "$B/libverbs_shared.so"
  gcc $CF src/app.c -o "$B/app_bsym" -L"$B" -rdynamic -lcollective -lverbs_shared -Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive
  echo "  DSO with    -Bsymbolic: $(run_variant app_bsym)"
} > "$ART/06_fix_ladder.txt"

log "SUMMARY"
{
  echo "SPLIT-STATE (mixed static/dynamic interposition) -- EVIDENCE SUMMARY"
  echo "generated on $(uname -m) / $(gcc -dumpmachine)"
  echo
  echo "bug-a (registration->shared copy, lookup->static empty): $(grep -c 'DEVICE NOT FOUND' "$ART/02_bug_a_run.txt") not-found -> $(grep discovered "$ART/02_bug_a_run.txt")"
  echo "bug-b (registration->static copy, lookup->shared empty): $(grep -c 'DEVICE NOT FOUND' "$ART/04_bug_b_run.txt") not-found -> $(grep discovered "$ART/04_bug_b_run.txt")"
  echo
  echo "See 03_ld_debug_bindings.txt for the dynamic-linker proof, 01_nm_* for the"
  echo "duplicate symbols, 05_nondeterminism.txt for the identical-source contrast,"
  echo "and 06_fix_ladder.txt for what fixes it and what does not."
} > "$ART/SUMMARY.txt"
cat "$ART/SUMMARY.txt"
echo; echo "artifacts:"; ls -1 "$ART"
