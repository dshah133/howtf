/* VENDOR verbs: vx_device_name only, in its own translation unit ->
 * its own archive member (vendor_name.o). The physical-id -> name mapping is
 * identical in both worlds; only the enumeration order in vx_open_device
 * differs. vx_device_name is still a DUPLICATE strong symbol across the two
 * archives, which matters for the explicit-error variant.
 */
#include "verbs.h"

const char *vx_device_name(int physical_id)
{
    static const char *const names[2] = { "training_nic", "storage_nic" };
    if (physical_id < 0 || physical_id > 1)
        return "<invalid>";
    return names[physical_id];
}
