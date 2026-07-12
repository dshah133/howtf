/* verbs.c - compiled TWICE into two copies of the same symbols:
 *   - into libverbs_shared.so   (with -DVX_ORIGIN="SHARED")
 *   - into libverbs_static.a    (with -DVX_ORIGIN="STATIC"), linked into the exe
 *
 * Each copy owns a PRIVATE file-scope table. The VX_ORIGIN tag is printed on
 * every register/lookup so the runtime trace shows exactly which copy ran.
 * A constructor (only when VX_WITH_CTOR is defined) registers two devices at
 * load time -- the "constructors ran elsewhere" part of the bug.
 */
#include <stdio.h>
#include "verbs.h"

#ifndef VX_ORIGIN
#define VX_ORIGIN "?"
#endif

static const char *table[8];
static int count = 0;

void vx_register_device(const char *name)
{
    if (count < 8)
        table[count++] = name;
    fprintf(stderr, "    [register -> copy=%s] this copy's table now holds %d device(s)\n",
            VX_ORIGIN, count);
}

int vx_get_device_list(const char **out, int max)
{
    int n = count < max ? count : max;
    fprintf(stderr, "    [get_list <- copy=%s] this copy holds %d device(s)\n",
            VX_ORIGIN, count);
    for (int i = 0; i < n; i++)
        out[i] = table[i];
    return n;
}

#ifdef VX_WITH_CTOR
__attribute__((constructor))
static void vx_boot(void)
{
    fprintf(stderr, "    [constructor in copy=%s] registering rxe_train, rxe_store\n", VX_ORIGIN);
    vx_register_device("rxe_train");
    vx_register_device("rxe_store");
}
#endif
