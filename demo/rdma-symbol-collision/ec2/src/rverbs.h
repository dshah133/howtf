/* rverbs.h - device-SELECTION API shared by the vendor and bundled verbs.
 *
 * Same collision as the local demo, but here the colliding symbol sits in front
 * of REAL rdma-core: vx_select_device() returns the NAME of the soft-RoCE
 * device a caller's logical index should map to, and the collective then calls
 * the real ibv_get_device_list / ibv_open_device on that name.
 *
 * Two real devices exist on the box:
 *   rxe_train  (soft-RoCE on ens5)       - the "training" NIC
 *   rxe_store  (soft-RoCE on rxedummy0)  - the "storage" NIC
 *
 * Vendor and bundled disagree on enumeration order, so "logical device 0"
 * selects a different PHYSICAL rdma device depending on which archive's
 * vx_select_device got linked in.
 */
#ifndef RVERBS_H
#define RVERBS_H

/* Return the rdma device name this implementation maps the logical index to. */
const char *vx_select_device(int logical_idx);

#endif /* RVERBS_H */
