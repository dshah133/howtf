/* collective.c - built into libcollective.so, a dynamically-linked collective.
 * It has an UNDEFINED reference to vx_get_device_list, resolved at runtime by
 * the dynamic linker against the global symbol scope. Which copy it binds to
 * (the executable's static copy, or libverbs_shared.so's copy) decides whether
 * it sees the devices the constructor registered.
 */
#include <stdio.h>
#include "verbs.h"

void collective_run(void)
{
    const char *list[8];
    int n = vx_get_device_list(list, 8);
    printf("  collective: discovered %d device(s)%s\n", n,
           n == 0
             ? "   *** DEVICE NOT FOUND -- but the constructor DID register devices, into the OTHER copy ***"
             : "");
    for (int i = 0; i < n; i++)
        printf("    [%d] %s\n", i, list[i]);
}
