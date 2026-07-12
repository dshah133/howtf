/* verbs.h - the "RDMA verbs" style device API that both vendor and bundled
 * implementations provide. Both archives define these SAME C-linkage symbols
 * (vx_open_device, vx_device_name) but enumerate devices in OPPOSITE order.
 *
 * Physical devices (stable identity, same in both worlds):
 *   physical 0 = "training_nic"   (the RoCE NIC carrying gradient allreduce)
 *   physical 1 = "storage_nic"    (the NIC carrying checkpoint / dataset I/O)
 *
 * The two implementations disagree only on ENUMERATION ORDER, i.e. what
 * physical device a caller's "logical index" maps to. That disagreement is the
 * whole bug: a caller that opens "logical device 0" gets a different physical
 * NIC depending on which archive's vx_open_device actually got linked in.
 */
#ifndef VERBS_H
#define VERBS_H

/* Map a logical device index to a physical device id, per this
 * implementation's enumeration order. Returns a physical device id. */
int vx_open_device(int logical_idx);

/* Map a physical device id to its stable name. */
const char *vx_device_name(int physical_id);

#endif /* VERBS_H */
