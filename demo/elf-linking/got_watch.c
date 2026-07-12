// got_watch.c — watches its OWN GOT slot for add() get patched by lazy binding.
// Build (see capture.sh): the slot's link-time vaddr offset is baked in via
//   -DSLOT_OFF=0x... (from: readelf -rW, the R_X86_64_JUMP_SLOT entry for add)
// and the PIE's runtime base is read from /proc/self/maps. No debugger needed.
//
// Deliberately does NOT take &add: taking a function's address makes the
// linker resolve it eagerly through .plt.got (pointer-equality rules), and
// the lazy JUMP_SLOT we want to watch would never exist.
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

extern int add(int a, int b);

static uintptr_t exe_base(void) {
    FILE *f = fopen("/proc/self/maps", "r");
    char line[512];
    uintptr_t base = 0;
    while (f && fgets(line, sizeof line, f)) {
        if (strstr(line, "got_watch")) { /* first mapping of this executable */
            sscanf(line, "%" SCNxPTR, &base);
            break;
        }
    }
    if (f) fclose(f);
    return base;
}

static void print_libmath_text_mapping(void) {
    FILE *f = fopen("/proc/self/maps", "r");
    char line[512];
    while (f && fgets(line, sizeof line, f)) {
        if (strstr(line, "libmath") && strstr(line, "r-xp")) {
            printf("  libmath.so code   : %s", line);
            break;
        }
    }
    if (f) fclose(f);
}

int main(void) {
    uintptr_t *slot = (uintptr_t *)(exe_base() + (uintptr_t)SLOT_OFF);
    printf("GOT slot for add lives at %p\n", (void *)slot);
    printf("  before first call : 0x%016" PRIxPTR "  <- points back into our own .plt\n", *slot);
    printf("  add(5, 10) returns: %d      <- first call takes the resolver detour\n", add(5, 10));
    printf("  after first call  : 0x%016" PRIxPTR "  <- patched!\n", *slot);
    print_libmath_text_mapping();
    return 0;
}
