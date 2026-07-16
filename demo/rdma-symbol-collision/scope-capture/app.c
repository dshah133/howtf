/* The application binary.
 *
 * Linked against libbundle.so (copy A) -- so copy A is a startup DT_NEEDED
 * dependency, in the global scope before main() runs. Two consumers, wired the
 * way the incident's two consumers were:
 *
 *   in-house library  -> linked to copy A -> reads registry A
 *   NCCL              -> dlopen(copy B, RTLD_LOCAL) + dlvsym on that handle
 *                        -> reads registry B
 *
 * The device the provider registered lands in A (global-scope capture), so the
 * in-house read sees it and the dlvsym-on-B read does not. Same device, two
 * answers, decided entirely by which copy each consumer's lookup reaches. */

#define _GNU_SOURCE   /* dlvsym */
#include <stdio.h>
#include <dlfcn.h>
#include "verbs.h"   /* get_device_list here binds to copy A at link time */

typedef int (*get_list_fn)(struct vdev **, int);

int main(void)
{
    struct vdev *devs[16];
    int n;

    fprintf(stderr, "=== app: libbundle (copy A) is a startup DT_NEEDED dep, in the global scope ===\n");

    /* NCCL-style acquisition of the system verbs: bare-name dlopen, RTLD_LOCAL.
     * registryB's constructor dlopens providerB, whose constructor registers a
     * device -- and that registration is captured by copy A via the global scope. */
    fprintf(stderr, "\n--- dlopen(\"libregistryB.so.1\", RTLD_LOCAL)  [acquire copy B] ---\n");
    void *hB = dlopen("libregistryB.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!hB) {
        fprintf(stderr, "dlopen(copy B) failed: %s\n", dlerror());
        return 2;
    }

    /* in-house consumer: its verbs calls resolved to copy A at link time. */
    fprintf(stderr, "\n--- in-house consumer: get_device_list() -> copy A ---\n");
    n = get_device_list(devs, 16);
    fprintf(stderr, "    in-house consumer sees %d device(s)%s\n",
            n, n ? "   [OK -- reads the copy the registration landed in]" : "");

    /* NCCL consumer: handle-scoped versioned lookup on copy B. */
    fprintf(stderr, "\n--- NCCL consumer: dlvsym(hB, \"get_device_list\", \"VERB_1.0\") -> copy B ---\n");
    get_list_fn gB = (get_list_fn)dlvsym(hB, "get_device_list", "VERB_1.0");
    if (!gB) {
        fprintf(stderr, "dlvsym failed: %s\n", dlerror());
        return 3;
    }
    n = gB(devs, 16);
    fprintf(stderr, "    NCCL consumer sees %d device(s)%s\n",
            n, n ? "" : "   *** No IB devices found -- the registration went to the OTHER copy ***");

    return 0;
}
