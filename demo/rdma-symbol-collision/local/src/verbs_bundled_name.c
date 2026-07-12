/* BUNDLED verbs: vx_device_name, in its own translation unit -> its own archive
 * member (bundled_name.o). Identical mapping to the vendor copy; still a
 * duplicate strong symbol across the two archives.
 */
#include "verbs.h"

const char *vx_device_name(int physical_id)
{
    static const char *const names[2] = { "training_nic", "storage_nic" };
    if (physical_id < 0 || physical_id > 1)
        return "<invalid>";
    return names[physical_id];
}
