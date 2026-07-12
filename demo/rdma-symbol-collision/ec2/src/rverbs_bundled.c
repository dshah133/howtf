/* BUNDLED verbs selection: storage NIC enumerated first (OPPOSITE order).
 *   logical 0 -> "rxe_store"
 *   logical 1 -> "rxe_train"
 * Same symbol name and C linkage as the vendor copy. In the collision link this
 * member is never pulled, so collective_b silently runs the vendor order.
 */
#include "rverbs.h"

const char *vx_select_device(int logical_idx)
{
    static const char *const order[2] = { "rxe_store", "rxe_train" };
    if (logical_idx < 0 || logical_idx > 1)
        return "<invalid>";
    return order[logical_idx];
}
