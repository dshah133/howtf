/* libproviderB.so -- "the system mlx5 provider".
 *
 * DT_NEEDED on libregistryB.so.1 (copy B) -- copy B is loaded right beside it,
 * as the provider's own declared dependency. Its ELF constructor announces the
 * device the way rdma-core providers do: a plain extern call to register_driver.
 *
 * That call is the hinge. It is an UNDEFINED IMPORT, not a function pointer and
 * not a dlsym. The dynamic linker resolves it against the GLOBAL scope first and
 * only then against this dlopen's own dependency group. The global scope holds
 * copy A (libbundle, exported at startup); copy B sits in the local group. So
 * global-first sends this registration into copy A -- captured -- even though
 * copy B is the provider's own DT_NEEDED dependency sitting right there. */

#include <stdio.h>
#include "verbs.h"

static struct vdev provB_dev = { "mlx5_from_providerB" };

__attribute__((constructor))
static void provB_register(void)
{
    fprintf(stderr,
            "[providerB] ctor: register_driver(%s)  [plain extern import -> resolved by scope, not by caller]\n",
            provB_dev.name);
    register_driver(&provB_dev);
}
