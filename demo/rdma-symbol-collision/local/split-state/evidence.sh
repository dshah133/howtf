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
WHOLE="-Wl,--whole-archive -lverbs_static -Wl,--no-whole-archive"
DSHARED='-DVX_ORIGIN="SHARED" -DVX_WITH_CTOR'
log(){ echo "=== $* ==="; }
trace(){ eval "$RUN ./$B/$1" 2>&1 | grep -iE "constructor in|register ->|get_list <-|discovered"; }

log "00 toolchain"
{ uname -a; gcc --version|head -1; ld --version|head -1; objcopy --version|head -1; } > "$ART/00_toolchain.txt"

make clean >/dev/null 2>&1; mkdir -p "$B"
gcc $CFPIC -shared src/collective.c -o "$B/libcollective.so"
gcc $CF -DVX_ORIGIN='"STATIC"' -c src/verbs.c -o "$B/verbs_static.o"; ar crs "$B/libverbs_static.a" "$B/verbs_static.o"

# ===========================================================================
log "01 four-configuration gating experiment (the centerpiece)"
# Two knobs: DSO self-binds? (default / -Bsymbolic-functions / -Bsymbolic) and
# does the exe export its static copy? (-rdynamic vs --exclude-libs,ALL).
{
  echo "# Split is real ONLY when the DSO self-binds AND the exe exports its copy."
  echo "# Watch the 'register ->' line (which copy the constructor wrote) vs the"
  echo "# 'get_list <-' line (which copy discovery read)."
  echo
  echo "===== (A) default DSO (no self-bind) + exe EXPORTS static copy ====="
  echo "# expected: NO split -- constructor is interposed onto the exe copy too"
  gcc $CFPIC $DSHARED -shared src/verbs.c -o "$B/libverbs_shared.so"
  gcc $CF src/app.c -o "$B/app_A" -L"$B" -rdynamic -lcollective -lverbs_shared $WHOLE
  trace app_A
  echo
  echo "===== (B) DSO -Bsymbolic-functions + exe EXPORTS static copy ====="
  echo "# expected: SPLIT -- constructor self-binds to the DSO copy; discovery reads the exe copy"
  gcc $CFPIC $DSHARED -Wl,-Bsymbolic-functions -shared src/verbs.c -o "$B/libverbs_shared.so"
  gcc $CF src/app.c -o "$B/app_B" -L"$B" -rdynamic -lcollective -lverbs_shared $WHOLE
  trace app_B
  echo
  echo "===== (C) DSO -Bsymbolic (full) + exe EXPORTS static copy ====="
  echo "# expected: SPLIT too"
  gcc $CFPIC $DSHARED -Wl,-Bsymbolic -shared src/verbs.c -o "$B/libverbs_shared.so"
  gcc $CF src/app.c -o "$B/app_C" -L"$B" -rdynamic -lcollective -lverbs_shared $WHOLE
  trace app_C
  echo
  echo "===== (D) DSO -Bsymbolic-functions + exe does NOT export (--exclude-libs,ALL) ====="
  echo "# expected: NO split -- discovery binds to the DSO copy, same one the constructor wrote"
  gcc $CFPIC $DSHARED -Wl,-Bsymbolic-functions -shared src/verbs.c -o "$B/libverbs_shared.so"
  gcc $CF src/app.c -o "$B/app_D" -L"$B" -rdynamic -lcollective -lverbs_shared $WHOLE -Wl,--exclude-libs,ALL
  trace app_D
  echo
  echo "VERDICT: split only in (B) and (C). (A) removes self-binding, (D) removes"
  echo "the exe's interposition -- either one alone eliminates the split."
} > "$ART/01_matrix.txt"

# ---- config B is the headline bug; build it for the remaining artifacts ----
gcc $CFPIC $DSHARED -Wl,-Bsymbolic-functions -shared src/verbs.c -o "$B/libverbs_shared.so"
gcc $CF src/app.c -o "$B/app_bug" -L"$B" -rdynamic -lcollective -lverbs_shared $WHOLE

log "02 nm: same vx_* symbols defined in BOTH the executable and the DSO"
{
  echo "# executable app_bug (T = defined global, exported via -rdynamic):"
  nm "$B/app_bug" | grep -E "vx_get_device_list|vx_register_device"
  echo
  echo "# libverbs_shared.so (its own copy of the same symbols):"
  nm -D "$B/libverbs_shared.so" | grep -E "vx_get_device_list|vx_register_device"
} > "$ART/02_nm_duplicate_symbols.txt"

log "03 LD_DEBUG=bindings: which copy the collective's discovery bound to"
rm -f /tmp/ld.* 2>/dev/null || true
eval "LD_DEBUG=bindings LD_DEBUG_OUTPUT=/tmp/ld $RUN ./$B/app_bug" >/dev/null 2>&1 || true
{
  echo "# The collective's vx_get_device_list reference binds to the EXECUTABLE"
  echo "# (app_bug) -- the empty static copy -- while the constructor filled the"
  echo "# SHARED copy. That split is the bug."
  echo
  cat /tmp/ld.* 2>/dev/null | grep -iE "vx_register_device|vx_get_device_list" | grep -i binding | sed -E 's#/lab/##g' | sort -u
} > "$ART/03_ld_debug_bindings.txt"

log "04 bug-b (inverse)"
gcc $CF -DVX_ORIGIN='"STATIC"' -DVX_WITH_CTOR -c src/verbs.c -o "$B/verbs_static_ctor.o"; ar crs "$B/libverbs_static_ctor.a" "$B/verbs_static_ctor.o"
gcc $CFPIC -DVX_ORIGIN='"SHARED"' -shared src/verbs.c -o "$B/libverbs_shared_noctor.so"
gcc $CF src/app.c -o "$B/app_bug_b" -L"$B" -lcollective -lverbs_shared_noctor \
    -Wl,--whole-archive -lverbs_static_ctor -Wl,--no-whole-archive -Wl,--exclude-libs,ALL
eval "$RUN ./$B/app_bug_b" > "$ART/04_bug_b_run.txt" 2>&1

log "05 nondeterminism (identical sources, one link token apart)"
gcc $CFPIC $DSHARED -Wl,-Bsymbolic-functions -shared src/verbs.c -o "$B/libverbs_shared.so"
gcc $CF src/app.c -o "$B/app_with_static"    -L"$B" -rdynamic -lcollective -lverbs_shared $WHOLE
gcc $CF src/app.c -o "$B/app_without_static" -L"$B" -rdynamic -lcollective -lverbs_shared
{
  echo "# app.c and both .so's are byte-identical; only the app link line differs."
  echo "app_with_static    (redundant static copy linked):  $(eval "$RUN ./$B/app_with_static"    2>/dev/null | grep discovered)"
  echo "app_without_static (single copy):                    $(eval "$RUN ./$B/app_without_static" 2>/dev/null | grep discovered)"
} > "$ART/05_nondeterminism.txt"

log "06 fix ladder + honest negatives"
rv(){ eval "$RUN ./$B/$1" 2>/dev/null | grep discovered; }
{
  echo "FIXES THAT WORK:"
  gcc $CF src/app.c -o "$B/app_fix_single" -L"$B" -rdynamic -lcollective -lverbs_shared
  echo "  fix-drop-duplicate : $(rv app_fix_single)"
  gcc $CF src/app.c -o "$B/app_fix_excl" -L"$B" -rdynamic -lcollective -lverbs_shared $WHOLE -Wl,--exclude-libs,ALL
  echo "  fix-exclude-libs   : $(rv app_fix_excl)"
  gcc $CFPIC $DSHARED -shared src/verbs.c -o "$B/libverbs_shared_raw.so"
  objcopy --redefine-sym vx_get_device_list=prov_vx_get_device_list --redefine-sym vx_register_device=prov_vx_register_device "$B/libverbs_shared_raw.so" "$B/libverbs_shared_ns.so"
  gcc $CFPIC -shared src/collective.c -o "$B/libcollective_raw.so"
  objcopy --redefine-sym vx_get_device_list=prov_vx_get_device_list "$B/libcollective_raw.so" "$B/libcollective_ns.so"
  gcc $CF src/app.c -o "$B/app_fix_ns" -L"$B" -rdynamic -l:libcollective_ns.so -l:libverbs_shared_ns.so $WHOLE
  echo "  fix-prefix-rename  : $(rv app_fix_ns)"
  echo
  echo "NAIVE FIXES THAT DO NOT WORK (they hide the wrong copy):"
  gcc $CFPIC -fvisibility=hidden $DSHARED -Wl,-Bsymbolic-functions -shared src/verbs.c -o "$B/libverbs_shared.so"
  gcc $CF src/app.c -o "$B/app_nofix_vis" -L"$B" -rdynamic -lcollective -lverbs_shared $WHOLE
  echo "  nofix-visibility     (-fvisibility=hidden on DSO): $(rv app_nofix_vis)"
  gcc $CFPIC $DSHARED -Wl,-Bsymbolic-functions -Wl,--version-script=src/hide-dso.map -shared src/verbs.c -o "$B/libverbs_shared.so"
  gcc $CF src/app.c -o "$B/app_nofix_vs" -L"$B" -rdynamic -lcollective -lverbs_shared $WHOLE
  echo "  nofix-version-script (local:* on DSO):             $(rv app_nofix_vs)"
} > "$ART/06_fix_ladder.txt"

log "SUMMARY"
{
  echo "SPLIT-STATE (mixed static/dynamic interposition) -- EVIDENCE SUMMARY"
  echo "generated on $(uname -m) / $(gcc -dumpmachine)"
  echo
  echo "GATING EXPERIMENT (01_matrix.txt) -- split occurs in exactly these cells:"
  grep -E "=====|discovered" "$ART/01_matrix.txt" | sed 's/^/  /'
  echo
  echo "bug-b inverse (04): $(grep discovered "$ART/04_bug_b_run.txt")"
  echo
  echo "See 02_nm_* (duplicate symbols), 03_ld_debug_bindings (dynamic-linker"
  echo "proof), 05_nondeterminism, and 06_fix_ladder for the rest."
} > "$ART/SUMMARY.txt"
cat "$ART/SUMMARY.txt"
echo; echo "artifacts:"; ls -1 "$ART"
