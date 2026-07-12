#ifndef RVERBS_H
#define RVERBS_H
/* Registry API. Both the static copy (in the exe) and the shared copy (in
 * libverbs_shared.so) define these. Each owns a PRIVATE table. A constructor in
 * the shared copy enumerates the REAL rdma devices into ITS table; if discovery
 * binds to the exe's static copy, it sees an empty table. */
void vx_register_device(const char *name);
int  vx_get_device_list(const char **out, int max);
#endif
