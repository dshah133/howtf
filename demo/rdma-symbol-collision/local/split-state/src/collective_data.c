/* collective_data.c - a collective that does discovery by reading the device
 * TABLE as data (not by calling vx_get_device_list). Used by the data-object
 * collision configs (D): it binds to whichever global `vx_devices` wins in the
 * dynamic scope, and prints that table's address so you can see which copy it
 * read. Built into libcollective_data.so.
 */
#include <stdio.h>

extern const char *vx_devices[];
extern int vx_devices_count;

void collective_run(void)
{
    fprintf(stderr, "    [collective reads DATA vx_devices@%p] count=%d\n",
            (void *)vx_devices, vx_devices_count);
    printf("  collective(data): discovered %d device(s)%s\n", vx_devices_count,
           vx_devices_count == 0
             ? "   *** DEVICE NOT FOUND -- constructor filled a DIFFERENT vx_devices ***"
             : "");
    for (int i = 0; i < vx_devices_count; i++)
        printf("    [%d] %s\n", i, vx_devices[i]);
}
