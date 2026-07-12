/* verbs.h - the "verbs" device-registry API. Both the statically-linked copy in
 * the executable and the copy inside libverbs_shared.so define these SAME
 * symbols. Each copy owns a PRIVATE device table (file-scope static in verbs.c),
 * reachable only through these functions. The bug is that registration and
 * discovery can bind to DIFFERENT copies, so the constructor fills one copy's
 * table while the collective reads the other copy's (empty) table.
 */
#ifndef VERBS_H
#define VERBS_H

/* Register a device into THIS copy's private table. */
void vx_register_device(const char *name);

/* Copy up to `max` device names from THIS copy's private table into `out`;
 * returns the count. */
int vx_get_device_list(const char **out, int max);

#endif /* VERBS_H */
