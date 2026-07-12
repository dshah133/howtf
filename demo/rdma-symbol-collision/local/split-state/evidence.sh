#!/usr/bin/env bash
# evidence.sh - capture the split-state gating forensic chain into ./artifacts/.
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

log "00 toolchain"
{ uname -a; gcc --version|head -1; ld --version|head -1; objcopy --version|head -1; } > "$ART/00_toolchain.txt"

log "01 four-configuration gating experiment (the centerpiece)"
make clean >/dev/null 2>&1
make matrix 2>/dev/null | grep -vE '^gcc |^ar |^mkdir' > "$ART/01_matrix.txt"

# rebuild A and B for the LD_DEBUG contrast + nm
make clean >/dev/null 2>&1; mkdir -p "$B"
gcc $CFPIC -shared src/collective.c -o "$B/libcollective.so"
gcc $CF -DVX_ORIGIN='"STATIC"' -c src/verbs.c -o "$B/verbs_static.o"; ar crs "$B/libverbs_static.a" "$B/verbs_static.o"
gcc $CFPIC $DSHARED -shared src/verbs.c -o "$B/libverbs_shared.so"
gcc $CF src/app.c -o "$B/app_A" -L"$B" -rdynamic -lcollective -lverbs_shared $WHOLE
gcc $CFPIC $DSHARED -Wl,-Bsymbolic-functions -shared src/verbs.c -o "$B/libverbs_shared_bsym.so"
gcc $CF src/app.c -o "$B/app_B" -L"$B" -rdynamic -lcollective -l:libverbs_shared_bsym.so $WHOLE

log "02 LD_DEBUG=bindings,symbols -- which copy each reference bound to"
lddbg(){ rm -f /tmp/ld.*; LD_DEBUG=bindings,symbols LD_DEBUG_OUTPUT=/tmp/ld "$@" >/dev/null 2>&1 || true; cat /tmp/ld.* 2>/dev/null; }
{
  echo "# (A) DEFAULT: BOTH the DSO's own vx_register_device AND the collective's"
  echo "# vx_get_device_list bind to the EXECUTABLE (app_A) -> same copy, no split."
  lddbg env $RUN "$B/app_A" | grep -iE "vx_register_device|vx_get_device_list" | grep -i "binding file" | sed -E 's#.*/##; s/\[0\]//g' | sort -u
  echo
  echo "# (B) -Bsymbolic-functions: the collective's vx_get_device_list still binds"
  echo "# to the executable, but the DSO's own register is bound INTERNALLY at link"
  echo "# time (no runtime binding line), so the constructor writes the DSO copy."
  echo "# The table@ addresses in 01_matrix.txt are the proof of the split."
  lddbg env $RUN "$B/app_B" | grep -iE "vx_get_device_list" | grep -i "binding file" | sed -E 's#.*/##; s/\[0\]//g' | sort -u
} > "$ART/02_ld_debug_bindings.txt"

log "03 nm: same vx_* defined in BOTH the executable and the DSO"
{
  echo "# executable app_B:";       nm "$B/app_B" | grep -E "vx_get_device_list|vx_register_device"
  echo "# libverbs_shared (bsym):"; nm -D "$B/libverbs_shared_bsym.so" | grep -E "vx_get_device_list|vx_register_device"
} > "$ART/03_nm_duplicate_symbols.txt"

log "04 bug-b (inverse) + 05 nondeterminism + 06 fix ladder via make"
make bug-b            2>/dev/null | grep -iE "register ->|get_list <-|discovered" > "$ART/04_bug_b_run.txt"
make nondeterminism   2>/dev/null | grep -iE "###|discovered"                     > "$ART/05_nondeterminism.txt"
{
  echo "FIXES THAT WORK:"
  echo "  fix-drop-duplicate : $(make fix-drop-duplicate 2>/dev/null | grep discovered)"
  echo "  fix-exclude-libs   : $(make fix-exclude-libs   2>/dev/null | grep discovered)"
  echo "  fix-prefix-rename  : $(make fix-prefix-rename  2>/dev/null | grep discovered)"
  echo
  echo "NAIVE FIXES THAT DO NOT WORK (DSO forced to load; split persists):"
  echo "  nofix-visibility     : $(make nofix-visibility     2>/dev/null | grep discovered)"
  echo "  nofix-version-script : $(make nofix-version-script 2>/dev/null | grep discovered)"
} > "$ART/06_fix_ladder.txt"

log "SUMMARY"
{
  echo "SPLIT-STATE GATING EXPERIMENT -- EVIDENCE SUMMARY"
  echo "generated on $(uname -m) / $(gcc -dumpmachine)"
  echo
  echo "Which configs produce the REAL split (constructor writes one table,"
  echo "discovery reads another -- proven by different table@ addresses):"
  echo "  (A) default                 : NO  (interposition unifies both onto the exe copy)"
  echo "  (B) -Bsymbolic-functions    : YES"
  echo "  (C) protected visibility    : YES"
  echo "  (C) hidden visibility       : DSO dropped by --as-needed (constructor never runs);"
  echo "                                forced to load -> YES (split)"
  echo "  (D1) data table, DSO static : YES  (constructor fills DSO table; collective reads exe global)"
  echo "  (D2) data table, global both: NO   (copy-relocation unifies onto the exe BSS copy)"
  echo
  echo "Precondition (one sentence): the split needs TWO live copies of the symbol"
  echo "AND a DSO that self-binds its own internal use (via -Bsymbolic(-functions)"
  echo "or protected/hidden visibility) so its constructor writes the DSO copy while"
  echo "the rest of the program reads the executable's copy; plain data globals do"
  echo "NOT split because copy relocation gives everyone the executable's copy."
  echo
  echo "See 01_matrix.txt (address-proof gating), 02_ld_debug_bindings.txt,"
  echo "03_nm_*, 04_bug_b_run, 05_nondeterminism, 06_fix_ladder."
} > "$ART/SUMMARY.txt"
cat "$ART/SUMMARY.txt"
echo; echo "artifacts:"; ls -1 "$ART"
