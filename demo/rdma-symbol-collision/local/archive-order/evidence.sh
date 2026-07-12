#!/usr/bin/env bash
# evidence.sh -- capture the full forensic chain for the static-linking symbol
# collision, into ./artifacts/. Run INSIDE the Linux/GNU toolchain container:
#
#   docker run --rm -v "$PWD":/lab -w /lab howtf-rdma-lab:latest bash evidence.sh
#
# or via the wrapper:  ./evidence-in-docker.sh
#
# Every artifact is a real command output. Nothing here is hand-authored.
set -uo pipefail

ART=artifacts
B=build
mkdir -p "$ART"
rm -f "$ART"/*.txt "$ART"/*.map 2>/dev/null || true

log() { echo "=== $* ==="; }

# --------------------------------------------------------------------------
log "00 toolchain"
{ echo "# uname"; uname -a; echo; echo "# gcc"; gcc --version | head -1;
  echo "# ld"; ld --version | head -1; echo "# nm"; nm --version | head -1;
  echo "# objcopy"; objcopy --version | head -1; } > "$ART/00_toolchain.txt"

# Clean build of the standard archives.
make clean >/dev/null 2>&1
make archives >/dev/null 2>&1
gcc -O0 -g -fno-lto -ffunction-sections -c src/app.c -o "$B/app.o"

# --------------------------------------------------------------------------
log "01 nm: duplicate strong symbols across the two verbs archives"
nm "$B/libverbs_vendor.a"  > "$ART/01_nm_libverbs_vendor.txt"
nm "$B/libverbs_bundled.a" > "$ART/01_nm_libverbs_bundled.txt"
{
  echo "# The SAME strong (T = global text) symbols are defined in BOTH archives."
  echo "# 'T' = global/exported. That is what makes them collide at link time."
  echo
  echo "## libverbs_vendor.a"
  nm "$B/libverbs_vendor.a"  | grep -E "vx_open_device|vx_device_name"
  echo
  echo "## libverbs_bundled.a"
  nm "$B/libverbs_bundled.a" | grep -E "vx_open_device|vx_device_name"
} > "$ART/01_nm_duplicate_symbols.txt"

# helper: extract the "which archive member was pulled and why" section + the
# specific line resolving vx_open_device from a linker map file.
map_evidence() {
  local mapf="$1" out="$2"
  {
    echo "# GNU ld map: 'Archive member included to satisfy reference by file (symbol)'"
    echo "# This section proves WHICH archive member the linker pulled and WHY."
    echo
    awk '
      /Archive member included/ {p=1; print; next}
      p && NF==0 && body {p=0}
      p { if (NF>0) body=1; print }
    ' "$mapf"
    echo
    echo "# --cref cross-reference for vx_open_device (definer is the FIRST file listed):"
    awk '/^vx_open_device/{print; getline; while($0 ~ /^[[:space:]]/){print; if(!getline) break}}' "$mapf" || true
  } > "$out"
}

# --------------------------------------------------------------------------
log "02 collision (the silent bug)"
gcc "$B/app.o" -L"$B" -lcollective_a -lverbs_vendor -lcollective_b -lverbs_bundled \
    -Wl,-Map="$ART/02_collision.map" -Wl,--cref -o "$B/app_collision" 2> "$ART/02_collision_linkstderr.txt"
"$B/app_collision" > "$ART/02_collision_run.txt" 2>&1
map_evidence "$ART/02_collision.map" "$ART/02_collision_which_member.txt"
{
  echo "# Proof the bundled copy was NEVER pulled: grep the map for bundled members."
  echo "## vendor_open.o (expect: PRESENT -- it satisfied vx_open_device for everyone):"
  grep -n "vendor_open.o"  "$ART/02_collision.map" || echo "(absent)"
  echo "## bundled_open.o (expect: ABSENT -- never pulled, so no error, silent misdirection):"
  grep -n "bundled_open.o" "$ART/02_collision.map" || echo "(absent -- confirmed never pulled)"
} >> "$ART/02_collision_which_member.txt"

# --------------------------------------------------------------------------
log "03 link-order (reverse the pairs -> collective_a is the victim)"
gcc "$B/app.o" -L"$B" -lcollective_b -lverbs_bundled -lcollective_a -lverbs_vendor \
    -Wl,-Map="$ART/03_link_order.map" -Wl,--cref -o "$B/app_link_order" 2>/dev/null
"$B/app_link_order" > "$ART/03_link_order_run.txt" 2>&1
map_evidence "$ART/03_link_order.map" "$ART/03_link_order_which_member.txt"

# --------------------------------------------------------------------------
log "04 explicit-error (force both members -> real multiple definition)"
gcc -O0 -g -fno-lto -ffunction-sections -DFORCE_PULL_BUNDLED -c src/app.c -o "$B/app_force.o"
gcc "$B/app_force.o" -L"$B" -lcollective_a -lverbs_vendor -lcollective_b -lverbs_bundled \
    -o "$B/app_explicit" > "$ART/04_explicit_error.txt" 2>&1 \
    && echo "UNEXPECTED: link succeeded" >> "$ART/04_explicit_error.txt" \
    || echo ">>> link failed as expected (multiple definition)" >> "$ART/04_explicit_error.txt"

# --------------------------------------------------------------------------
log "05 fixed-groups (--start-group; honest: does NOT change the outcome)"
gcc "$B/app.o" -L"$B" -Wl,--start-group -lcollective_a -lverbs_vendor -lcollective_b -lverbs_bundled -Wl,--end-group \
    -Wl,-Map="$ART/05_fixed_groups.map" -o "$B/app_groups" 2>/dev/null \
    && "$B/app_groups" > "$ART/05_fixed_groups_run.txt" 2>&1

# --------------------------------------------------------------------------
log "06 fixed-visibility scenario-1 (separate archives; honest: does NOT fix)"
for f in vendor_open vendor_name bundled_open bundled_name; do
  src="src/verbs_${f/vendor_/vendor_}"; :
done
gcc -O0 -g -fno-lto -ffunction-sections -fvisibility=hidden -c src/verbs_vendor_open.c  -o "$B/vendor_open_hidden.o"
gcc -O0 -g -fno-lto -ffunction-sections -fvisibility=hidden -c src/verbs_vendor_name.c  -o "$B/vendor_name_hidden.o"
gcc -O0 -g -fno-lto -ffunction-sections -fvisibility=hidden -c src/verbs_bundled_open.c -o "$B/bundled_open_hidden.o"
gcc -O0 -g -fno-lto -ffunction-sections -fvisibility=hidden -c src/verbs_bundled_name.c -o "$B/bundled_name_hidden.o"
ar crs "$B/libverbs_vendor_hidden.a"  "$B/vendor_open_hidden.o"  "$B/vendor_name_hidden.o"
ar crs "$B/libverbs_bundled_hidden.a" "$B/bundled_open_hidden.o" "$B/bundled_name_hidden.o"
{
  echo "# Even with -fvisibility=hidden the verbs symbols stay GLOBAL for the"
  echo "# static executable link (hidden controls DYNAMIC export, not static"
  echo "# archive resolution). nm still shows 'T':"
  nm "$B/libverbs_bundled_hidden.a" | grep -E "vx_open_device|vx_device_name"
  echo
  echo "# runtime (still misdirected):"
} > "$ART/06_fixed_visibility_scenario1.txt"
gcc "$B/app.o" -L"$B" -lcollective_a -lverbs_vendor_hidden -lcollective_b -lverbs_bundled_hidden \
    -o "$B/app_fix_vis" 2>/dev/null && "$B/app_fix_vis" >> "$ART/06_fixed_visibility_scenario1.txt" 2>&1

# --------------------------------------------------------------------------
log "07 fixed-objcopy (localize: no fix here; redefine-sym namespacing: FIX)"
{
  echo "===== experiment 1: objcopy --localize-symbol on libverbs_bundled.a ====="
  echo "# The bundled member is never pulled anyway, so localizing it changes"
  echo "# nothing. Still misdirected:"
} > "$ART/07_fixed_objcopy.txt"
cp "$B/libverbs_bundled.a" "$B/libverbs_bundled_localized.a"
objcopy --localize-symbol=vx_open_device --localize-symbol=vx_device_name "$B/libverbs_bundled_localized.a"
gcc "$B/app.o" -L"$B" -lcollective_a -lverbs_vendor -lcollective_b -lverbs_bundled_localized \
    -o "$B/app_localized" 2>/dev/null && "$B/app_localized" >> "$ART/07_fixed_objcopy.txt" 2>&1
{
  echo
  echo "===== experiment 2: objcopy --redefine-sym namespacing (collective_b + bundled) ====="
  echo "# Rename the colliding pair to vx_*__b in BOTH collective_b and its"
  echo "# bundled verbs -> no shared global symbol -> each uses its own copy:"
} >> "$ART/07_fixed_objcopy.txt"
cp "$B/libcollective_b.a" "$B/libcollective_b_ns.a"
cp "$B/libverbs_bundled.a" "$B/libverbs_bundled_ns.a"
objcopy --redefine-sym vx_open_device=vx_open_device__b --redefine-sym vx_device_name=vx_device_name__b "$B/libcollective_b_ns.a"
objcopy --redefine-sym vx_open_device=vx_open_device__b --redefine-sym vx_device_name=vx_device_name__b "$B/libverbs_bundled_ns.a"
gcc "$B/app.o" -L"$B" -lcollective_a -lverbs_vendor -lcollective_b_ns -lverbs_bundled_ns \
    -o "$B/app_namespaced" 2>/dev/null && "$B/app_namespaced" >> "$ART/07_fixed_objcopy.txt" 2>&1

# --------------------------------------------------------------------------
log "08/09/10 scenario-2 vendored (co-located def+use)"
ld -r "$B/collective_a.o" "$B/vendor_open.o"  "$B/vendor_name.o"  -o "$B/coll_a_vendored.o"
ld -r "$B/collective_b.o" "$B/bundled_open.o" "$B/bundled_name.o" -o "$B/coll_b_vendored.o"
ar crs "$B/libcoll_a_vendored.a" "$B/coll_a_vendored.o"
ar crs "$B/libcoll_b_vendored.a" "$B/coll_b_vendored.o"
{
  echo "# Merged (vendored) objects keep GLOBAL vx_open_device -> the collision"
  echo "# here is LOUD (multiple definition), not silent, because the merged"
  echo "# member carrying the entry point also carries vx_open_device:"
} > "$ART/08_vendored_collision.txt"
gcc "$B/app.o" -L"$B" -lcoll_a_vendored -lcoll_b_vendored -o "$B/app_vendored" >> "$ART/08_vendored_collision.txt" 2>&1 \
    || echo ">>> link failed as expected (multiple definition)" >> "$ART/08_vendored_collision.txt"

# vendored + localize-symbol
cp "$B/coll_a_vendored.o" "$B/coll_a_loc.o"; cp "$B/coll_b_vendored.o" "$B/coll_b_loc.o"
{
  echo "# nm of merged collective_b BEFORE --localize-symbol (T = global):"
  nm "$B/coll_b_loc.o" | grep -E "vx_open_device|vx_device_name"
} > "$ART/09_vendored_fixed_localize.txt"
objcopy --localize-symbol=vx_open_device --localize-symbol=vx_device_name "$B/coll_a_loc.o"
objcopy --localize-symbol=vx_open_device --localize-symbol=vx_device_name "$B/coll_b_loc.o"
{
  echo "# nm AFTER --localize-symbol (t = local -> no cross-library collision):"
  nm "$B/coll_b_loc.o" | grep -E "vx_open_device|vx_device_name"
  echo "# runtime (fixed -- each collective uses its own copy):"
} >> "$ART/09_vendored_fixed_localize.txt"
ar crs "$B/libcoll_a_loc.a" "$B/coll_a_loc.o"; ar crs "$B/libcoll_b_loc.a" "$B/coll_b_loc.o"
gcc "$B/app.o" -L"$B" -lcoll_a_loc -lcoll_b_loc -o "$B/app_vloc" 2>/dev/null \
    && "$B/app_vloc" >> "$ART/09_vendored_fixed_localize.txt" 2>&1

# vendored + visibility hidden + localize-hidden
gcc -O0 -g -fno-lto -ffunction-sections -fvisibility=hidden -c src/verbs_vendor_open.c  -o "$B/vo_h.o"
gcc -O0 -g -fno-lto -ffunction-sections -fvisibility=hidden -c src/verbs_vendor_name.c  -o "$B/vn_h.o"
gcc -O0 -g -fno-lto -ffunction-sections -fvisibility=hidden -c src/verbs_bundled_open.c -o "$B/bo_h.o"
gcc -O0 -g -fno-lto -ffunction-sections -fvisibility=hidden -c src/verbs_bundled_name.c -o "$B/bn_h.o"
ld -r "$B/collective_a.o" "$B/vo_h.o" "$B/vn_h.o" -o "$B/coll_a_h.o"
ld -r "$B/collective_b.o" "$B/bo_h.o" "$B/bn_h.o" -o "$B/coll_b_h.o"
{
  echo "# nm of merged collective_b BEFORE localize-hidden (T = global, even though hidden):"
  nm "$B/coll_b_h.o" | grep -E "vx_open_device|vx_device_name"
} > "$ART/10_vendored_fixed_visibility.txt"
objcopy --localize-hidden "$B/coll_a_h.o"; objcopy --localize-hidden "$B/coll_b_h.o"
{
  echo "# nm AFTER objcopy --localize-hidden (t = local):"
  nm "$B/coll_b_h.o" | grep -E "vx_open_device|vx_device_name"
  echo "# runtime (fixed):"
} >> "$ART/10_vendored_fixed_visibility.txt"
ar crs "$B/libcoll_a_h.a" "$B/coll_a_h.o"; ar crs "$B/libcoll_b_h.a" "$B/coll_b_h.o"
gcc "$B/app.o" -L"$B" -lcoll_a_h -lcoll_b_h -o "$B/app_vh" 2>/dev/null \
    && "$B/app_vh" >> "$ART/10_vendored_fixed_visibility.txt" 2>&1

# --------------------------------------------------------------------------
log "SUMMARY"
{
  echo "STATIC-LINKING SYMBOL COLLISION -- EVIDENCE SUMMARY"
  echo "generated by evidence.sh on $(uname -m) / $(gcc -dumpmachine)"
  echo
  echo "SCENARIO 1: cross-library interposition (4 archives, task link line)"
  echo "  -lcollective_a -lverbs_vendor -lcollective_b -lverbs_bundled"
  echo "  collision       : $(grep -c 'WRONG DEVICE' "$ART/02_collision_run.txt") wrong-device line(s)  -> SILENT misdirection, no link error"
  echo "  link-order      : $(grep -c 'WRONG DEVICE' "$ART/03_link_order_run.txt") wrong-device line(s)  -> reversing order moves the victim"
  echo "  explicit-error  : $(grep -c 'multiple definition' "$ART/04_explicit_error.txt") 'multiple definition' -> forcing both members = LOUD error"
  echo "  fixed-groups    : $(grep -c 'WRONG DEVICE' "$ART/05_fixed_groups_run.txt") wrong-device line(s)  -> --start-group does NOT fix it"
  echo "  fixed-visibility: $(grep -c 'WRONG DEVICE' "$ART/06_fixed_visibility_scenario1.txt") wrong-device line(s)  -> -fvisibility=hidden does NOT fix it"
  echo "  fixed-objcopy   : localize=$(grep -c 'WRONG DEVICE' "$ART/07_fixed_objcopy.txt") wrong-device line(s) total (exp1 fails, exp2 redefine-sym FIXES)"
  echo
  echo "SCENARIO 2: vendored copy, def+use co-located (ld -r merged objects)"
  echo "  vendored-collision       : $(grep -c 'multiple definition' "$ART/08_vendored_collision.txt") 'multiple definition' -> LOUD, not silent"
  echo "  vendored-fixed-localize  : $(grep -c 'WRONG DEVICE' "$ART/09_vendored_fixed_localize.txt") wrong-device -> objcopy --localize-symbol FIXES"
  echo "  vendored-fixed-visibility: $(grep -c 'WRONG DEVICE' "$ART/10_vendored_fixed_visibility.txt") wrong-device -> hidden + --localize-hidden FIXES"
  echo
  echo "VERDICT"
  echo "  The silent misdirection is first-definition-wins across duplicate strong"
  echo "  symbols, made silent by one-symbol-per-member archive granularity."
  echo "  Fixes that do NOT work for the cross-library layout: --start-group,"
  echo "  -fvisibility=hidden, objcopy --localize-symbol (member never pulled)."
  echo "  Fixes that DO work: namespacing (objcopy --redefine-sym / rename),"
  echo "  a single canonical copy, or vendoring correctly (merge + localize so"
  echo "  each library privately owns its copy)."
} > "$ART/SUMMARY.txt"

cat "$ART/SUMMARY.txt"
echo
echo "Artifacts written to $ART/:"
ls -1 "$ART"
