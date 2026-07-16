#ifndef VERBS_H
#define VERBS_H

/* A minimal model of the rdma-core registration/discovery contract.
 * struct vdev stands in for a verbs device; register_driver models
 * verbs_register_driver_<N> (the provider-facing registration entry point);
 * get_device_list models ibv_get_device_list (the consumer-facing discovery
 * entry point). Every instance of the "verbs" library keeps its own file-static
 * registry, exactly as rdma-core keeps driver_list/device_list per instance. */

struct vdev { const char *name; };

/* provider -> registry: append a device to *this instance's* registry */
void register_driver(struct vdev *d);

/* consumer <- registry: read *this instance's* registry */
int get_device_list(struct vdev **out, int max);

#endif
