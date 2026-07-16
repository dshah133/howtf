/* libbundle.so -- COPY A.
 *
 * Models the verbs stack bundled into the training binary and carved into a
 * link group: a genuine shared library, wired to the app through DT_NEEDED, its
 * boundary symbols exported into the process's GLOBAL dynamic scope at startup.
 * No -Bsymbolic-functions, no protected visibility -- plain default-visibility
 * exports. The whole point is that scope alone, not any self-binding flag, is
 * what captures the provider's registration into this copy. */

#include <stdio.h>
#include "verbs.h"

/* copy A's own file-static registry (rdma-core's per-instance driver_list) */
static struct vdev *reg_a[16];
static int reg_a_n;

void register_driver(struct vdev *d)
{
    if (reg_a_n < 16)
        reg_a[reg_a_n++] = d;
    fprintf(stderr,
            "[bundle / copy A] register_driver(%s) -> registry A @%p now holds %d device(s)\n",
            d->name, (void *)reg_a, reg_a_n);
}

int get_device_list(struct vdev **out, int max)
{
    int i;
    for (i = 0; i < reg_a_n && i < max; i++)
        out[i] = reg_a[i];
    fprintf(stderr,
            "[bundle / copy A] get_device_list  <- registry A @%p holds %d device(s)\n",
            (void *)reg_a, reg_a_n);
    return i;
}
