/* VENDOR verbs selection: training NIC enumerated first.
 *   logical 0 -> "rxe_train"
 *   logical 1 -> "rxe_store"
 * Own translation unit -> own archive member, so the linker can pull this copy
 * for everyone and skip the bundled copy entirely.
 */
#include "rverbs.h"

const char *vx_select_device(int logical_idx)
{
    static const char *const order[2] = { "rxe_train", "rxe_store" };
    if (logical_idx < 0 || logical_idx > 1)
        return "<invalid>";
    return order[logical_idx];
}
