/* collective_b: checkpoint/storage collective. Built against BUNDLED selection,
 * which puts the storage NIC at logical 0. Expects to open rxe_store -- but in
 * the collision link the bundled copy is never pulled, so vx_select_device(0)
 * silently returns the VENDOR answer (rxe_train) and this opens the wrong real
 * rdma device. */
#include "rverbs.h"
#include "rdma_util.h"

void collective_b_run(void)
{
    const char *selected = vx_select_device(0);
    struct ibv_context *ctx = open_rdma_by_name(selected);
    report("collective_b (checkpoint/storage)", "rxe_store", selected, ctx);
    if (ctx) ibv_close_device(ctx);
}
