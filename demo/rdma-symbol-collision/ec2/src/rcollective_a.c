/* collective_a: training collective. Built against VENDOR selection, which puts
 * the training NIC at logical 0. Expects to open rxe_train. */
#include "rverbs.h"
#include "rdma_util.h"

void collective_a_run(void)
{
    const char *selected = vx_select_device(0);
    struct ibv_context *ctx = open_rdma_by_name(selected);
    report("collective_a (training/allreduce)", "rxe_train", selected, ctx);
    if (ctx) ibv_close_device(ctx);
}
