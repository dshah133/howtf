/* rdma_util: the REAL rdma-core calls. Not a colliding symbol; compiled into
 * its own object and passed directly on the link line so it is always present.
 */
#include <stdio.h>
#include <string.h>
#include <endian.h>
#include <infiniband/verbs.h>
#include "rdma_util.h"

struct ibv_context *open_rdma_by_name(const char *want)
{
    int n = 0;
    struct ibv_device **list = ibv_get_device_list(&n);
    struct ibv_context *ctx = NULL;
    if (!list)
        return NULL;
    for (int i = 0; i < n; i++) {
        if (strcmp(ibv_get_device_name(list[i]), want) == 0) {
            ctx = ibv_open_device(list[i]);
            break;
        }
    }
    ibv_free_device_list(list);
    return ctx;
}

void report(const char *who, const char *expected, const char *selected,
            struct ibv_context *ctx)
{
    const char *opened = ctx ? ibv_get_device_name(ctx->device) : "<open failed>";
    unsigned long long guid =
        ctx ? (unsigned long long)be64toh(ibv_get_device_guid(ctx->device)) : 0ULL;
    int wrong = strcmp(opened, expected) != 0;
    printf("  %-34s selected=%-9s opened=%-9s guid=%016llx  [expected %-9s]  %s\n",
           who, selected, opened, guid, expected,
           wrong ? "*** WRONG DEVICE ***" : "OK");
}
