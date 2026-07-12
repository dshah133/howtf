/* VENDOR verbs: vx_open_device only. Kept in its own translation unit so it
 * lands in its own archive member (vendor_open.o). That one-symbol-per-member
 * layout is what lets the linker satisfy a vx_open_device reference from THIS
 * archive without dragging in anything else -- and, crucially, lets it SKIP the
 * bundled archive's matching member entirely once this one has been pulled.
 *
 * Vendor enumeration order: training NIC first.
 *   logical 0 -> physical 0 (training_nic)
 *   logical 1 -> physical 1 (storage_nic)
 */
#include "verbs.h"

int vx_open_device(int logical_idx)
{
    static const int enumeration_order[2] = { 0, 1 }; /* training first */
    if (logical_idx < 0 || logical_idx > 1)
        return -1;
    return enumeration_order[logical_idx];
}
