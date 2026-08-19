// teardown-hang.c — the NCCL 2.17.1 commReclaim hang, in miniature. No GPUs required.
//
// This is a Linux socket-lifetime reproducer, not an NCCL execution reproducer.
// It implements the liveness-critical post-abort state of NCCL 2.17.1's proxy
// service loop — the service is stopping (stop == 1), one accepted connection
// remains counted (npeers == 1), and the aborting owner sends no application-
// level Close message — and demonstrates the fork-duplicated-descriptor delay
// and the effect of the 2.18.1 shutdown()-before-close() change. It does not
// model the production trigger, the identity of the fd holder, or NCCL's
// proxy-connection topology (commonly a self-connection; see the article).
// Mid-hang, it samples /proc/net/tcp to show the kernel's own view of the
// connection: ESTABLISHED, after the closing process is already dead.
//
// Three parties:
//   "svc":    the service-thread role — waits for npeers to reach 0, draining
//             it only on a peer-visible close (EOF) or socket error.
//   "rank":   the aborting owner of the client endpoint; tears down with
//             close() alone (2.17.1) or shutdown()+close() (2.18.1).
//   "helper": a child fork()ed by rank after the connection existed — it
//             inherits a duplicate descriptor and never touches it. (A generic
//             forked helper; stock PyTorch DataLoader workers have parent-
//             death machinery and would not outlive a dead rank for long.)
//
// Build:  gcc -std=gnu11 -O2 -Wall -Wextra -Wpedantic -o teardown-hang teardown-hang.c
// Run:    ./teardown-hang
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define CHECK(x)  do { if ((x) < 0)  { perror(#x); _exit(1); } } while (0)
#define CHECK1(x) do { if ((x) != 1) { fprintf(stderr, "%s failed\n", #x); _exit(1); } } while (0)

static double t0;
static double now(void) {
  struct timespec ts; CHECK(clock_gettime(CLOCK_MONOTONIC, &ts));
  return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}
static void msleep(long ms) {
  struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };
  while (nanosleep(&ts, &ts) == -1) {
    if (errno != EINTR) { perror("nanosleep"); _exit(1); }
  }
}
#define LOG(who, fmt, ...) \
  printf("%-7s " fmt "  [t=%.2fs]\n", who ":", ##__VA_ARGS__, now() - t0)

static void rank_role(int use_shutdown, int port) {
  int fd; CHECK(fd = socket(AF_INET, SOCK_STREAM, 0));
  struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons((uint16_t)port) };
  CHECK1(inet_pton(AF_INET, "127.0.0.1", &a.sin_addr));
  CHECK(connect(fd, (struct sockaddr*)&a, sizeof a));
  LOG("rank", "connected to the service endpoint (descriptor aliases: 1)");

  int ack[2]; CHECK(pipe(ack));         // fork() itself is the inheritance
  pid_t helper; CHECK(helper = fork()); // point: the child's fd table is a
  if (helper == 0) {                    // copy, socket duplicate included.
    close(ack[0]);
    ssize_t n;
    do { n = write(ack[1], "k", 1); } while (n < 0 && errno == EINTR);
    if (n != 1) _exit(2);
    close(ack[1]);
    msleep(3700);                       // ...holds the fd, does nothing...
    _exit(0);
  }
  close(ack[1]);                        // rank must NOT hold the write end,
  char k; ssize_t n;                    // or a dead helper couldn't unblock us
  do { n = read(ack[0], &k, 1); } while (n < 0 && errno == EINTR);
  if (n != 1 || k != 'k') { fprintf(stderr, "helper acknowledgment failed\n"); _exit(2); }
  close(ack[0]);
  LOG("rank", "forked helper pid %d — fd table copied (descriptor aliases: 2)", (int)helper);

  msleep(300);                          // ...then the abort path runs:
  if (use_shutdown) {
    LOG("rank", "teardown: shutdown(fd, SHUT_RDWR); close(fd)   // 2.18.1");
    CHECK(shutdown(fd, SHUT_RDWR));
    CHECK(close(fd));
  } else {
    LOG("rank", "teardown: close(fd)                            // 2.17.1");
    CHECK(close(fd));                   // one alias released. not the last.
  }
  LOG("rank", "rank process exited");
  _exit(0);                             // even process death doesn't help:
}                                       // the helper still holds an alias.

static void dump_established(int port) { // the kernel's view, straight from
  char hex[8];                           // /proc/net/tcp: st 01 = ESTABLISHED
  snprintf(hex, sizeof hex, ":%04X", port);
  FILE* f = fopen("/proc/net/tcp", "r");
  if (!f) return;                        // best-effort observation
  char line[512]; int shown = 0;
  while (fgets(line, sizeof line, f)) {
    unsigned sl, st; char l[64], r[64];
    if (sscanf(line, " %u: %63s %63s %x", &sl, l, r, &st) == 4 &&
        st == 0x01 && (strstr(l, hex) || strstr(r, hex))) {
      if (!shown++) LOG("svc", "sampling /proc/net/tcp — the kernel's view of the connection:");
      printf("          %s %s st=01 (ESTABLISHED)\n", l, r);
    }
  }
  fclose(f);
}

int main(void) {
  setvbuf(stdout, NULL, _IOLBF, 0);
  CHECK(prctl(PR_SET_CHILD_SUBREAPER, 1)); // orphaned grandchildren (the
                                           // helpers) reparent to us, so the
                                           // final waitpid() loop reaps them.
  const char* title[2] = {
    "experiment 1 · teardown with close() only            (NCCL 2.17.1 behavior)",
    "experiment 2 · teardown with shutdown() then close() (NCCL 2.18.1 behavior)" };

  for (int mode = 0; mode < 2; mode++) {
    printf("\n== %s ==\n", title[mode]);
    int lfd; CHECK(lfd = socket(AF_INET, SOCK_STREAM, 0));
    int one = 1; CHECK(setsockopt(lfd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one));
    struct sockaddr_in a = { .sin_family = AF_INET };
    CHECK1(inet_pton(AF_INET, "127.0.0.1", &a.sin_addr));
    CHECK(bind(lfd, (struct sockaddr*)&a, sizeof a));
    socklen_t alen = sizeof a; CHECK(getsockname(lfd, (struct sockaddr*)&a, &alen));
    CHECK(listen(lfd, 1));
    int port = ntohs(a.sin_port);

    t0 = now();
    pid_t rank; CHECK(rank = fork());
    if (rank == 0) { close(lfd); rank_role(mode, port); }

    int fd; CHECK(fd = accept(lfd, NULL, NULL));
    LOG("svc", "accepted the connection: npeers = 1");

    // NCCL 2.17.1's service-loop liveness condition, reduced: the abort flag
    // is already observed (stop == 1); the loop persists while npeers > 0,
    // waking at most every 500 ms (quiescent timeout); npeers drains only
    // on EOF or a socket error.
    int npeers = 1, rounds = 0, warned = 0, sampled = 0;
    while (npeers > 0) {
      struct pollfd p = { .fd = fd, .events = POLLIN };
      int r = poll(&p, 1, 500);
      if (r < 0 && errno == EINTR) continue;
      CHECK(r); rounds++;
      if (r > 0 && (p.revents & (POLLIN | POLLHUP | POLLERR | POLLNVAL))) {
        char buf[64];
        ssize_t n = recv(fd, buf, sizeof buf, MSG_DONTWAIT);
        if (n == 0) {
          LOG("svc", "EOF (recv() == 0) — a peer-visible close arrived. npeers-- -> 0");
          npeers--;
        } else if (n < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) {
          LOG("svc", "socket error (%s) — connection retired. npeers-- -> 0", strerror(errno));
          npeers--;
        }
      } else {
        if (mode == 0 && rounds == 3 && !sampled) { sampled = 1; dump_established(port); }
        if (!warned && now() - t0 > 3.0) {
          LOG("svc", "*** %d poll rounds, no event. npeers still 1.", rounds);
          LOG("svc", "*** the service loop cannot exit -> commFree stays in pthread_join");
          LOG("svc", "*** (this is the hang. waiting to see when the close becomes visible...)");
          warned = 1;
        }
      }
    }
    LOG("svc", "service loop exits; pthread_join would return; ncclCommAbort completes");
    if (mode == 1)
      printf("        note: the helper is STILL ALIVE, still holding its duplicate\n"
             "        descriptor. shutdown() acted on the shared socket, not the alias count.\n");
    CHECK(close(fd)); CHECK(close(lfd));
    for (;;) {                            // reap rank + (reparented) helper
      pid_t w = waitpid(-1, NULL, mode == 0 ? 0 : WNOHANG);
      if (w > 0) continue;
      if (w == 0) { msleep(100); continue; }
      if (errno == ECHILD) break;
      if (errno == EINTR) continue;
      perror("waitpid"); _exit(1);
    }
  }
  return 0;
}
