/* collective_b: a "checkpoint / storage" collective. It is built and tested
 * against the BUNDLED verbs, whose enumeration puts the storage NIC at logical
 * index 0. It opens logical device 0 and expects to be talking to
 * "storage_nic".
 *
 * The trap: on the link line collective_b's bundled verbs archive comes AFTER
 * the vendor archive. By the time the linker reaches collective_b's
 * vx_open_device reference, that symbol is already satisfied by the vendor
 * copy, so the bundled copy is never pulled. collective_b silently runs the
 * vendor enumeration and opens the TRAINING NIC instead of the storage NIC.
 */
#include <stdio.h>
#include "verbs.h"

void collective_b_run(void)
{
    int phys = vx_open_device(0);
    const char *name = vx_device_name(phys);
    printf("  collective_b (checkpoint/storage): opened logical dev 0 -> physical %d -> %-12s [expected storage_nic]   %s\n",
           phys, name,
           (name && !__builtin_strcmp(name, "storage_nic")) ? "OK" : "*** WRONG DEVICE ***");
}
