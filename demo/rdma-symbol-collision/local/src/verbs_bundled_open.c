/* BUNDLED verbs: vx_open_device, in its own translation unit -> its own archive
 * member (bundled_open.o). Same symbol name and C linkage as the vendor copy,
 * but the OPPOSITE enumeration order: storage NIC first.
 *   logical 0 -> physical 1 (storage_nic)
 *   logical 1 -> physical 0 (training_nic)
 *
 * This member also defines vx_bundled_probe(), CO-LOCATED with vx_open_device
 * in the same object file. It is unused by the silent-collision build. The
 * explicit-error build references it on purpose: referencing a co-located
 * symbol forces the linker to pull THIS member, which then also drags in this
 * member's vx_open_device -- now colliding with the vendor copy already pulled,
 * producing a real "multiple definition" error. That contrast (silent vs.
 * hard error) is entirely an accident of member granularity and what else you
 * happen to reference.
 */
#include "verbs.h"

int vx_open_device(int logical_idx)
{
    static const int enumeration_order[2] = { 1, 0 }; /* storage first */
    if (logical_idx < 0 || logical_idx > 1)
        return -1;
    return enumeration_order[logical_idx];
}

/* Co-located marker used only by the explicit-error variant to force this
 * member to be pulled. */
void vx_bundled_probe(void)
{
    /* intentionally empty */
}
