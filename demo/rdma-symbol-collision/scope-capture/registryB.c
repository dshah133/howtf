/* libregistryB.so.1 -- COPY B ("the system libibverbs").
 *
 * The same source as copy A, built as a separate shared object with its own
 * SONAME. NCCL reaches this copy the way it really does: a bare-name
 * dlopen("libibverbs.so.1", RTLD_LOCAL), then handle-scoped dlvsym for each
 * entry point. Because the dlopen is RTLD_LOCAL, this copy's names never enter
 * the global scope -- so nothing a provider registers through the global scope
 * can ever land here.
 *
 * Its constructor dlopens the hardware provider, exactly as libibverbs dlopens
 * the mlx5 driver named in its config. */

#include <stdio.h>
#include <dlfcn.h>
#include "verbs.h"

/* copy B's own file-static registry -- a different object from copy A's */
static struct vdev *reg_b[16];
static int reg_b_n;

void register_driver(struct vdev *d)
{
    if (reg_b_n < 16)
        reg_b[reg_b_n++] = d;
    fprintf(stderr,
            "[registryB / copy B] register_driver(%s) -> registry B @%p now holds %d device(s)\n",
            d->name, (void *)reg_b, reg_b_n);
}

int get_device_list(struct vdev **out, int max)
{
    int i;
    for (i = 0; i < reg_b_n && i < max; i++)
        out[i] = reg_b[i];
    fprintf(stderr,
            "[registryB / copy B] get_device_list  <- registry B @%p holds %d device(s)\n",
            (void *)reg_b, reg_b_n);
    return i;
}

/* libibverbs dlopens its provider; so does copy B. */
__attribute__((constructor))
static void load_provider(void)
{
    fprintf(stderr, "[registryB / copy B] ctor: dlopen(libproviderB.so) -- the provider beside me\n");
    if (!dlopen("libproviderB.so", RTLD_NOW | RTLD_LOCAL))
        fprintf(stderr, "[registryB / copy B] dlopen provider FAILED: %s\n", dlerror());
}
