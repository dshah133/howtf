/*
 * dontfork.c — a hardware-free model of the libibverbs fork-safety hazard.
 *
 * On the fork-safe path (ibv_fork_init() / RDMAV_FORK_SAFE=1), every
 * ibv_reg_mr() rounds the registered byte range out to page boundaries and
 * marks those pages MADV_DONTFORK (rdma-core, libibverbs/memory.c).
 * fake_reg_mr() below performs exactly that madvise, with no RNIC needed.
 *
 * Two modes:
 *   ./dontfork demo    a 4-byte scratch word shares a page with state the
 *                      forked child needs  ->  the child dies with SIGSEGV
 *   ./dontfork fixed   the scratch word owns a dedicated page-rounded
 *                      allocation          ->  the child is fine
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

static long PAGE;

/* What ibv_reg_mr() does to your pages when fork safety is armed. */
static void fake_reg_mr(void *addr, size_t len)
{
    uintptr_t start = (uintptr_t)addr & ~((uintptr_t)PAGE - 1);
    uintptr_t end = ((uintptr_t)addr + len + PAGE - 1) & ~((uintptr_t)PAGE - 1);

    if (madvise((void *)start, end - start, MADV_DONTFORK) != 0) {
        perror("madvise(MADV_DONTFORK)");
        exit(1);
    }
    printf("reg_mr: asked for %zu bytes at %p\n", len, addr);
    printf("reg_mr: marked DONTFORK [%p, %p) = %ld bytes\n",
           (void *)start, (void *)end, (long)(end - start));
}

/* Print the kernel's own view: VmFlags of the mapping holding addr. */
static void show_vmflags(void *addr)
{
    FILE *f = fopen("/proc/self/smaps", "r");
    if (!f)
        return;
    char line[512];
    uintptr_t lo = 0, hi = 0, a = (uintptr_t)addr;
    int in_range = 0;
    while (fgets(line, sizeof line, f)) {
        if (sscanf(line, "%lx-%lx ", &lo, &hi) == 2)
            in_range = (a >= lo && a < hi);
        else if (in_range && strncmp(line, "VmFlags:", 8) == 0) {
            printf("smaps:  %s", line);
            break;
        }
    }
    fclose(f);
}

/* The transport's connection state, as one ordinary heap object. */
struct recv_comm {
    int flush_word;             /* 4-byte GDR-flush scratch (registered) */
    char neighbor_state[64];    /* unrelated state the child will need   */
};

int main(int argc, char **argv)
{
    PAGE = sysconf(_SC_PAGESIZE);
    const char *mode = argc > 1 ? argv[1] : "demo";
    printf("page size: %ld bytes\n", PAGE);

    /* Page-aligned so the demo layout is deterministic: both fields
     * provably share one page, as a malloc'd struct often would. */
    struct recv_comm *comm = NULL;
    if (posix_memalign((void **)&comm, PAGE, sizeof *comm) != 0)
        return 1;
    snprintf(comm->neighbor_state, sizeof comm->neighbor_state,
             "epoch=41 step=118000");
    printf("layout: flush_word at %p, neighbor_state at %p (same page)\n",
           (void *)&comm->flush_word, (void *)comm->neighbor_state);

    if (strcmp(mode, "fixed") == 0) {
        /* The fix: RDMA-owned state gets its own page-rounded pages. */
        int *flush = NULL;
        if (posix_memalign((void **)&flush, PAGE, PAGE) != 0)
            return 1;
        fake_reg_mr(flush, sizeof *flush);
    } else {
        /* The incident: register the 4-byte field in the shared page. */
        fake_reg_mr(&comm->flush_word, sizeof comm->flush_word);
    }
    show_vmflags(&comm->flush_word);

    printf("parent: neighbor_state = \"%s\"\n", comm->neighbor_state);
    fflush(stdout); /* don't let the child inherit buffered output */

    pid_t pid = fork();
    if (pid == 0) {
        printf("child:  dereferencing the same pointer...\n");
        fflush(stdout);
        printf("child:  neighbor_state = \"%s\"\n", comm->neighbor_state);
        _exit(0);
    }

    int st = 0;
    waitpid(pid, &st, 0);
    if (WIFSIGNALED(st))
        printf("parent: child killed by signal %d (%s)\n",
               WTERMSIG(st), strsignal(WTERMSIG(st)));
    else
        printf("parent: child exited %d\n", WEXITSTATUS(st));

    printf("parent: neighbor_state still \"%s\"\n", comm->neighbor_state);
    return 0;
}
