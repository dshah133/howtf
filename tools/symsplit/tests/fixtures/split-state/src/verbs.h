/* verbs.h - shared declarations for the split-state fixture. */
#ifndef VERBS_H
#define VERBS_H
void vx_register_device(const char *name);
int vx_get_device_list(const char **out, int max);
#endif
