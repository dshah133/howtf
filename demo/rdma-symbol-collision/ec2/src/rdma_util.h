#ifndef RDMA_UTIL_H
#define RDMA_UTIL_H
#include <infiniband/verbs.h>

/* Open the real rdma device whose name matches `want` (or NULL). */
struct ibv_context *open_rdma_by_name(const char *want);

/* Print what was selected vs. what was actually opened. */
void report(const char *who, const char *expected, const char *selected,
            struct ibv_context *ctx);

#endif /* RDMA_UTIL_H */
