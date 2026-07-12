/* verbs.c - compiled into two copies of the same symbols: one in
 * libverbs_shared.so (-DVX_ORIGIN="SHARED"), one in libverbs_static.a
 * (-DVX_ORIGIN="STATIC") that is linked into the executable.
 *
 * Each register/lookup prints WHICH copy ran (VX_ORIGIN) and the ADDRESS of
 * that copy's device table -- so "constructor wrote one table, discovery read
 * another" is provable by comparing addresses, not just by trusting a label.
 *
 * Build knobs used by the gating experiment (Makefile `matrix`):
 *   VX_WITH_CTOR      define to compile the load-time constructor into a copy.
 *   VX_TABLE_GLOBAL   make the device table a GLOBAL symbol `vx_devices`
 *                     (default: file-scope static, private to the copy). Used
 *                     by the data-object-collision configs (D).
 *   VX_VIS_HIDDEN /   give the verbs functions + table hidden / protected
 *   VX_VIS_PROTECTED  visibility inside the DSO (config C).
 */
#include <stdio.h>

#ifndef VX_ORIGIN
#define VX_ORIGIN "?"
#endif

#ifdef VX_VIS_HIDDEN
#  define VXVIS __attribute__((visibility("hidden")))
#elif defined(VX_VIS_PROTECTED)
#  define VXVIS __attribute__((visibility("protected")))
#else
#  define VXVIS
#endif

#ifdef VX_TABLE_GLOBAL
VXVIS const char *vx_devices[8];
VXVIS int vx_devices_count = 0;
#else
static const char *vx_devices[8];
static int vx_devices_count = 0;
#endif

VXVIS void vx_register_device(const char *name)
{
    if (vx_devices_count < 8)
        vx_devices[vx_devices_count++] = name;
    fprintf(stderr, "    [register -> copy=%s table@%p] now holds %d device(s)\n",
            VX_ORIGIN, (void *)vx_devices, vx_devices_count);
}

VXVIS int vx_get_device_list(const char **out, int max)
{
    int n = vx_devices_count < max ? vx_devices_count : max;
    fprintf(stderr, "    [get_list <- copy=%s table@%p] this copy holds %d device(s)\n",
            VX_ORIGIN, (void *)vx_devices, vx_devices_count);
    for (int i = 0; i < n; i++)
        out[i] = vx_devices[i];
    return n;
}

#ifdef VX_WITH_CTOR
__attribute__((constructor))
static void vx_boot(void)
{
    fprintf(stderr, "    [constructor in copy=%s] registering rxe_train, rxe_store\n", VX_ORIGIN);
    vx_register_device("rxe_train");
    vx_register_device("rxe_store");
}
#endif
