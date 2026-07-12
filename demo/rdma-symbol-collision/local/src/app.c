/* app: the training job. It runs both collectives. In a correct world
 * collective_a targets the training NIC and collective_b targets the storage
 * NIC. With the silent symbol collision, collective_b is misdirected onto the
 * training NIC.
 */
#include <stdio.h>

void collective_a_run(void);
void collective_b_run(void);

#ifdef FORCE_PULL_BUNDLED
/* Referencing this co-located symbol from the bundled archive forces the
 * linker to pull bundled_open.o, which also carries a second definition of
 * vx_open_device -> real "multiple definition" error at link time. */
void vx_bundled_probe(void);
#endif

int main(void)
{
    printf("== training job: initializing collectives ==\n");
#ifdef FORCE_PULL_BUNDLED
    vx_bundled_probe();
#endif
    collective_a_run();
    collective_b_run();
    printf("== done ==\n");
    return 0;
}
