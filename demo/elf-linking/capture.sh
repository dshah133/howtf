#!/bin/bash
# capture.sh — runs INSIDE the ubuntu:22.04 amd64 container (see regenerate.sh).
# Rebuilds the demo and captures every artifact the post shows into artifacts/.
set -eu # no pipefail: `cmd | head` SIGPIPEs are expected and benign here
cd /code
A=/code/artifacts
mkdir -p "$A"

echo "== toolchain versions =="
{ gcc --version | head -1; ld --version | head -1; ldd --version | head -1; } | tee "$A/versions.txt"

make clean >/dev/null || true
make all 2>&1 | tee "$A/make-output.txt"

# --- compile-time story (Part V) ---
gcc -c main.c
readelf -r main.o > "$A/readelf-r-main-o.txt"
objdump -d main.o > "$A/objdump-main-o.txt"
gcc -v -o /tmp/link-probe main.o -L. -lmath -Wl,-rpath,'$ORIGIN' 2>&1 |
  grep -E 'collect2|/ld ' | head -2 > "$A/gcc-v-link.txt" || true
rm -f /tmp/link-probe

# --- the default (BIND_NOW) binary ---
readelf -h ./dynamic_app > "$A/readelf-h.txt"
readelf -lW ./dynamic_app > "$A/readelf-l.txt"
readelf -d ./dynamic_app > "$A/readelf-d-default.txt"
readelf -rW ./dynamic_app > "$A/readelf-rW.txt"
readelf -W --dyn-syms ./dynamic_app > "$A/readelf-dynsyms.txt"
readelf -p .dynstr ./dynamic_app > "$A/readelf-dynstr.txt"
readelf -V ./dynamic_app > "$A/readelf-V.txt"
objdump -d -j .text ./dynamic_app | sed -n '/<main>:/,/^$/p' > "$A/objdump-main-default.txt"
objdump -d -j .plt -j .plt.got -j .plt.sec ./dynamic_app > "$A/objdump-plt-default.txt" || true

# --- the explicit-lazy variant (Part III walkthrough) ---
readelf -d ./dynamic_app_lazy > "$A/readelf-d-lazy.txt"
readelf -lW ./dynamic_app_lazy | grep -A2 GNU_RELRO > "$A/relro-lazy.txt"
readelf -lW ./dynamic_app | grep -A2 GNU_RELRO > "$A/relro-default.txt"
readelf -rW ./dynamic_app_lazy > "$A/readelf-rW-lazy.txt"
objdump -d -j .text ./dynamic_app_lazy | sed -n '/<main>:/,/^$/p' > "$A/objdump-main-lazy.txt"
objdump -d -j .plt -j .plt.sec ./dynamic_app_lazy > "$A/objdump-plt-lazy.txt" || true

# --- ASLR: two runs, bias differs (values captured under emulation; see post) ---
for run in 1 2; do
  ./dynamic_app & pid=$!
  sleep 0.3
  grep -E 'dynamic_app|libmath|libc|ld-linux|\[stack\]' "/proc/$pid/maps" \
    > "$A/maps-run$run.txt"
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
done

# --- lazy binding, watched live: the process reads its own GOT slot for add()
#     before and after the first call. No debugger needed (ptrace is unavailable
#     under Rosetta emulation, and this works anywhere). ---
readelf -x .got.plt ./dynamic_app_lazy > "$A/got-plt-initial-lazy.txt"
# two-pass: the slot offset must come from got_watch's OWN .got.plt layout
# (pass 2 only changes an integer constant in .text, so the layout is stable)
gcc -o got_watch got_watch.c -L. -lmath -Wl,-z,lazy -Wl,-rpath,'$ORIGIN' -DSLOT_OFF=0x0
SLOT_OFF=$(readelf -rW ./got_watch |
  awk '$3 == "R_X86_64_JUMP_SLOT" && $5 == "add" {print $1}')
gcc -o got_watch got_watch.c -L. -lmath -Wl,-z,lazy -Wl,-rpath,'$ORIGIN' \
  -DSLOT_OFF=0x"$SLOT_OFF"
LAZY_SLOT_OFF=$(readelf -rW ./dynamic_app_lazy |
  awk '$3 == "R_X86_64_JUMP_SLOT" && $5 == "add" {print $1}')
echo "JUMP_SLOT for add: got_watch 0x$SLOT_OFF · dynamic_app_lazy 0x$LAZY_SLOT_OFF" \
  > "$A/got-slot-offset.txt"
{
  echo "\$ ./got_watch   # built like dynamic_app_lazy, plus -DSLOT_OFF=0x$SLOT_OFF"
  ./got_watch
} > "$A/got-watch-run.txt" 2>&1

# --- error #2: DT_NEEDED [./libmath.so] is CWD-relative; $ORIGIN fixes it ---
gcc -o dynamic_app_broken main.c ./libmath.so -Wl,-rpath,'$ORIGIN' 2>&1
readelf -d ./dynamic_app_broken | grep -E 'NEEDED|RUNPATH' > "$A/broken-needed.txt"
readelf -d ./dynamic_app | grep -E 'NEEDED|RUNPATH' > "$A/fixed-needed.txt"
{
  echo '# run from /  (broken build: DT_NEEDED is ./libmath.so)'
  (cd / && /code/dynamic_app_broken 2>&1 || true) | head -2
  echo '# run from /  (fixed build: DT_NEEDED is libmath.so + RUNPATH $ORIGIN)'
  (cd / && timeout 1 /code/dynamic_app 2>&1 || true) | head -2
  echo '# (exit via timeout; the fixed binary runs its sleep(60) happily)'
} > "$A/error2-cwd-relative.txt"
{
  cd /
  LD_DEBUG=libs timeout 0.5 /code/dynamic_app 2>&1 | grep -E 'find library|search path|trying file' | head -8
} > "$A/error2-ld-debug.txt" || true

echo "capture complete:"; ls -la "$A" | tail -30
