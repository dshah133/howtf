/* collective_a: a "training" collective (think gradient allreduce). It is
 * built and tested against the VENDOR verbs, whose enumeration puts the
 * training NIC at logical index 0. It opens logical device 0 and expects to be
 * talking to "training_nic".
 */
#include <stdio.h>
#include "verbs.h"

void collective_a_run(void)
{
    int phys = vx_open_device(0);
    const char *name = vx_device_name(phys);
    printf("  collective_a (training/allreduce): opened logical dev 0 -> physical %d -> %-12s [expected training_nic]  %s\n",
           phys, name,
           (name && !__builtin_strcmp(name, "training_nic")) ? "OK" : "*** WRONG DEVICE ***");
}
