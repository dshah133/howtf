# nccl-teardown-fin

Hardware-free reproducer for the post *howtf did the training job hang
after the training was done?*

`teardown-hang.c` reduces NCCL 2.17.1's post-abort proxy service loop to
its liveness-critical core: the abort flag is already observed, one
accepted connection remains counted (`npeers == 1`), and no
application-level Close message is coming. A helper `fork()`ed after the
connection existed holds a duplicate of the client descriptor. Experiment 1
tears down with `close()` alone (NCCL 2.17.1's `ncclSocketClose`); the
service side sees nothing until the helper exits. Experiment 2 adds
`shutdown(SHUT_RDWR)` first (the 2.18.1 fix); the EOF is immediate while
the helper still holds its descriptor.

This is a Linux socket-lifetime reproducer, not an NCCL execution
reproducer. It needs Linux for `/proc/net/tcp` and
`PR_SET_CHILD_SUBREAPER`.

## Run

```sh
gcc -std=gnu11 -O2 -Wall -Wextra -Wpedantic -o teardown-hang teardown-hang.c
./teardown-hang
```

On macOS/Windows, run it in a container:

```sh
docker run --rm -v "$PWD":/code -w /code gcc:13 \
  sh -c 'gcc -std=gnu11 -O2 -Wall -Wextra -Wpedantic -o teardown-hang teardown-hang.c && ./teardown-hang'
```

## Representative output

```
== experiment 1 · teardown with close() only            (NCCL 2.17.1 behavior) ==
rank:   connected to the service endpoint (descriptor aliases: 1)  [t=0.00s]
rank:   forked helper pid 503 — fd table copied (descriptor aliases: 2)  [t=0.00s]
svc:    accepted the connection: npeers = 1  [t=0.00s]
rank:   teardown: close(fd)                            // 2.17.1  [t=0.30s]
rank:   rank process exited  [t=0.30s]
svc:    sampling /proc/net/tcp — the kernel's view of the connection:  [t=1.51s]
          0100007F:98A7 0100007F:BB4E st=01 (ESTABLISHED)
          0100007F:BB4E 0100007F:98A7 st=01 (ESTABLISHED)
svc:    *** 6 poll rounds, no event. npeers still 1.  [t=3.01s]
svc:    *** the service loop cannot exit -> commFree stays in pthread_join  [t=3.01s]
svc:    EOF (recv() == 0) — a peer-visible close arrived. npeers-- -> 0  [t=3.70s]
svc:    service loop exits; pthread_join would return; ncclCommAbort completes  [t=3.70s]

== experiment 2 · teardown with shutdown() then close() (NCCL 2.18.1 behavior) ==
rank:   forked helper pid 506 — fd table copied (descriptor aliases: 2)  [t=0.00s]
rank:   teardown: shutdown(fd, SHUT_RDWR); close(fd)   // 2.18.1  [t=0.30s]
svc:    EOF (recv() == 0) — a peer-visible close arrived. npeers-- -> 0  [t=0.30s]
svc:    service loop exits; pthread_join would return; ncclCommAbort completes  [t=0.30s]
        note: the helper is STILL ALIVE, still holding its duplicate
        descriptor. shutdown() acted on the shared socket, not the alias count.
```
