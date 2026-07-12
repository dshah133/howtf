/* rapp: training job driver. Lists the real rdma devices, then runs both
 * collectives against real ibv_open_device. */
#include <stdio.h>
#include <infiniband/verbs.h>

void collective_a_run(void);
void collective_b_run(void);

int main(void)
{
    int n = 0;
    struct ibv_device **list = ibv_get_device_list(&n);
    printf("== real rdma devices visible (%d) ==\n", n);
    for (int i = 0; i < n; i++)
        printf("  [%d] %s\n", i, ibv_get_device_name(list[i]));
    if (list) ibv_free_device_list(list);

    printf("== training job: initializing collectives against REAL rdma ==\n");
    collective_a_run();
    collective_b_run();
    printf("== done ==\n");
    return 0;
}
