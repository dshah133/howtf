---
title: "howtf did the training job hang after the training was done?"
description: "An NCCL 2.17.1 deep dive: an abort path waiting for a peer-visible close, a file descriptor duplicated by fork(), and the shutdown() that 2.18.1 added."
date: 2026-08-18
tags: [nccl, pytorch, linux, tcp, fork, sockets]
draft: false
---

## 1. The job that wouldn't die

The training was fine. That was the problem.

Loss curves converged, the last checkpoint landed on disk, every training-step collective completed and produced the right answer. And then, some evenings, the job just sat there. GPUs idle, no error, no crash, no exit code. A cluster slot burning until an external watchdog reaped the whole thing, often enough to become a recurring SEV.

A hang in production is, in principle, a solved diagnostic problem: there is telemetry everywhere, you can snapshot a machine, you can trace a process. In practice the first hard question is not *why* something is hung but *who*. A single training host is a small zoo:

<figure class="frame diagram">
  <span class="frame-title">fig. 1 · one training host, many suspects</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 330" role="img" aria-label="Diagram: a training host contains rank processes. Rank 0 holds a main trainer thread, an NCCL Service thread which is highlighted, an NCCL Progress thread, and the ProcessGroupNCCL watchdog, plus forked dataloader workers and checkpoint helper processes. Rank 1 and further ranks have the same shape.">
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="14" y="20" width="692" height="288" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="28" y="40" fill="var(--muted)">host · 8 GPUs</text>
        <rect x="40" y="54" width="400" height="180" fill="var(--seg)" opacity="0.08"/>
        <rect x="40" y="54" width="400" height="180" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="54" y="74" fill="var(--seg)">rank 0 process</text>
        <rect x="56" y="86" width="368" height="22" fill="none" stroke="var(--muted)" stroke-width="1"/>
        <text x="64" y="101" fill="var(--text)">main trainer thread</text>
        <text x="416" y="101" text-anchor="end" fill="var(--muted)" font-size="10">Python + CUDA</text>
        <rect x="56" y="114" width="368" height="22" fill="var(--accent)" opacity="0.10"/>
        <rect x="56" y="114" width="368" height="22" fill="none" stroke="var(--accent)" stroke-width="1.6"/>
        <text x="64" y="129" fill="var(--accent)">"NCCL Service 0" thread</text>
        <text x="416" y="129" text-anchor="end" fill="var(--muted)" font-size="10">per-communicator proxy service</text>
        <rect x="56" y="142" width="368" height="22" fill="none" stroke="var(--muted)" stroke-width="1"/>
        <text x="64" y="157" fill="var(--text)">"NCCL Progress 0" thread</text>
        <text x="416" y="157" text-anchor="end" fill="var(--muted)" font-size="10">progress engine</text>
        <rect x="56" y="170" width="368" height="22" fill="none" stroke="var(--muted)" stroke-width="1"/>
        <text x="64" y="185" fill="var(--text)">ProcessGroupNCCL watchdog</text>
        <text x="416" y="185" text-anchor="end" fill="var(--muted)" font-size="10">PyTorch's timeout police</text>
        <text x="64" y="216" fill="var(--muted)" font-size="10">+ whatever the script itself is doing</text>
        <line x1="440" y1="100" x2="470" y2="84" stroke="var(--muted)" stroke-width="1"/>
        <line x1="440" y1="130" x2="470" y2="146" stroke="var(--muted)" stroke-width="1"/>
        <rect x="470" y="64" width="216" height="40" fill="var(--seg)" opacity="0.08"/>
        <rect x="470" y="64" width="216" height="40" fill="none" stroke="var(--seg)" stroke-width="1.2"/>
        <text x="482" y="81" fill="var(--seg)">dataloader workers</text>
        <text x="482" y="96" fill="var(--muted)" font-size="10">fork()ed child processes</text>
        <rect x="470" y="126" width="216" height="40" fill="var(--seg)" opacity="0.08"/>
        <rect x="470" y="126" width="216" height="40" fill="none" stroke="var(--seg)" stroke-width="1.2"/>
        <text x="482" y="143" fill="var(--seg)">checkpoint / uploader helpers</text>
        <text x="482" y="158" fill="var(--muted)" font-size="10">also forked children</text>
        <rect x="40" y="248" width="400" height="30" fill="none" stroke="var(--seg)" stroke-width="1" stroke-dasharray="5 4"/>
        <text x="54" y="267" fill="var(--muted)">rank 1 process — same shape</text>
        <text x="54" y="298" fill="var(--muted)">… ranks 2–7, then multiply by every host</text>
      </g>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--seg)"></span>process</span>
      <span><span class="k" style="background:var(--accent)"></span>the thread teardown turns out to wait on</span>
    </p>
  </div>
</figure>

Multiply by every host. "The job is hung" ranges over hundreds of processes, most of which are *supposed* to be waiting for something. So the first real work was elimination: walk the snapshots, find the thread that should be making progress and isn't. Training threads: done. Dataloaders: idle, waiting for a next-batch request. Checkpointing: finished.

The thread that should have been making progress, and wasn't, was inside NCCL. Not in a collective. In *teardown*.

> **Scope note.** This article separates production observations from mechanism reconstruction. The captured stacks and process state place the hang in NCCL's teardown; the NCCL 2.17.1 source, Linux descriptor semantics, and the 2.18.1 patch establish the duplicated-descriptor mechanism. Those claims are checked against pinned tags ([v2.17.1-1](https://github.com/NVIDIA/nccl/tree/v2.17.1-1), [v2.18.1-1](https://github.com/NVIDIA/nccl/tree/v2.18.1-1), [diff](https://github.com/NVIDIA/nccl/compare/v2.17.1-1...v2.18.1-1)) and, where it mattered, re-tested on a live kernel. The surviving evidence does *not* conclusively identify which forked helper retained the descriptor in our incident, or which timeout first sent PyTorch into its abort path; those incident-specific details are labeled as reconstruction below. The fix ships in 2.18.1 with a source comment that reads like this article's abstract. Hold that thought for §8.

## 2. The thread that was "idle"

Per-thread stacks off a hung host converge on one silhouette, reproduced here from the public record ([NVIDIA/nccl#992](https://github.com/NVIDIA/nccl/issues/992)), whose shape matches what our capture pinned at the NCCL layers:

```text title="the silhouette (public #992 frame names; the NCCL frames are the ones our capture pinned)"
Thread (idle):
    (libc)                       ← parked, nothing progressing
    commFree      (init.cc)
    commCleanup   (init.cc)
    commReclaim   (init.cc)
    ncclCommAbort (init.cc)
    c10d::NCCLComm::ncclCommAbort
    c10d::abortCommsFromMap
    c10d::ProcessGroupNCCL::abort
```

Two observations before reading a line of NCCL code.

**Nobody wrote `ncclCommAbort` in the training script.** The caller is PyTorch: `ProcessGroupNCCL`'s error-handling path walks the map of live communicators and aborts each. The walk PyTorch ≥ 2.1 names [`abortCommsFromMap`](https://github.com/pytorch/pytorch/blob/v2.1.0/torch/csrc/distributed/c10d/ProcessGroupNCCL.cpp#L850); 2.0-era sources spell the same role differently. A common driver is the watchdog thread reacting to a collective timeout or an async error. Which timeout first sent *our* job down this path is reconstruction (by the time we captured it, the abort was already in flight) but the entry point is not: the thing that hangs is the *fault-handling* path, the code whose entire reason to exist is to get out unconditionally. The escape hatch jammed.

(Two cautions about that silhouette, in the interest of honesty. First, it is *nonspecific*: #992 was reported on 2.18.1, *after* the fix this article ends with, so the stack alone does not identify a mechanism. It tells you where teardown waits, not why; the why takes the rest of this article. Second, its `c10d` frame names are version-sensitive: `abortCommsFromMap` is absent from PyTorch v2.0.1 and present by v2.1.0, while the frames *below* the c10d layer are the ones our capture pinned, so the public c10d spelling is no evidence of our incident's PyTorch version. An affected-era report closer to ours is [#863](https://github.com/NVIDIA/nccl/issues/863): `ncclCommAbort` hanging in a process-exit test after upgrading to 2.16.5.)

**The bottom frame is not the network and not CUDA.** Chase it into NCCL 2.17.1 and the parked call is a `pthread_join`: the first substantive cleanup step in [`commFree`](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/init.cc#L169-L178) is to wait for the communicator's proxy service thread to exit.

```c title="NCCL init.cc @ v2.17.1-1, lines 169–178: the wait at the bottom of the abort"
static ncclResult_t commFree(ncclComm_t comm) {
  /* commFree() should not involve any sync among ranks. */
  if (comm == NULL)
    return ncclSuccess;
  ...
  if (comm->proxyState.thread)
    pthread_join(comm->proxyState.thread, nullptr);
```

That reframes the investigation. The hung thread is only a *mourner*: it waits for another thread of its own process. The real question is what the **service thread** is waiting for. Here the snapshots wrong-foot you, because the service thread does not look stuck at all. It is awake, waking from `poll()` (at most every 500 ms in its quiescent state), checking its exit condition, finding it unmet, and going back to sleep. Nothing on the host is blocked in the classic sense. The job is hung on an *absence*.

To see what event it wants, you need to know what sockets NCCL owns. NCCL owns more TCP than its reputation suggests.

## 3. NCCL's TCP control plane (smaller than I first thought, and stranger)

NCCL is "the GPU communication library," so the mental model defaults to NVLink lanes and InfiniBand verbs. That describes the **data plane**. The **control plane**, how ranks find each other and coordinate setup and teardown, runs its central pieces, the bootstrap network and the proxy-control channels, over ordinary TCP (Unix-domain sockets and shared memory play supporting roles), and it exists in every topology, including a single box where the GPUs talk over NVLink. Three layers, bottom up:

**The bootstrap ring.** Absent an externally supplied `NCCL_COMM_ID`, the process that calls `ncclGetUniqueId` opens a listening TCP socket whose address rides inside the ID; every rank connects, and NCCL wires the ranks into a ring of TCP connections for out-of-band allgathers during init ([bootstrap.cc](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/bootstrap.cc)).

**The proxy service thread.** Every communicator spawns a thread you've seen in `NCCL_DEBUG=INFO` output: `NCCL Service %2d` ([proxy.cc:1526–1528](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/proxy.cc#L1526-L1528) — the comment above the `pthread_create` says, precisely, that this thread "is pthread_join()'d by commFree()"). It listens on its own TCP socket; connections are accepted from ranks *on the same node* (the peer table is sized `NCCL_MAX_LOCAL_RANKS`).

**The per-transport control connections — and here is where I had the topology wrong in an earlier draft.** [`ncclProxyConnect`](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/proxy.cc#L982-L1005) takes a *selected proxy rank*, resolves it to a local-rank index, dials that rank's service thread over TCP, and caches one persistent connection per selected local proxy rank in `peerSocks[]`. The set of connections is lazily built and topology-dependent — **not** an all-to-all mesh. And in the common direct-P2P path, the selected rank is surprising:

```c title="NCCL transport setup @ v2.17.1-1: who actually gets dialed"
p2p.cc:240   info->rank = myInfo->rank;         // direct P2P send → MYSELF
p2p.cc:291   info->rank = myInfo->rank;         // direct P2P recv → MYSELF
p2p.cc:251   info->rank = intermediateRank;     // indirect P2P    → another local rank
shm.cc:158   ncclProxyConnect(..., comm->rank, ...);   // SHM (only if CE memcpy) → MYSELF
net.cc:177   ncclProxyConnect(..., proxyRank, ...);    // NET send → selected proxyRank: self without PXN, another local rank with PXN
nvls.cc:134  ncclProxyConnect(..., rank, ...);         // NVLS fd import → the OTHER rank
```

For the workhorse case, direct P2P between GPUs, a rank connects to **its own service thread**. The proxy handling a GPU's resources is the proxy of the rank that owns them, so both send and receive setup select `myInfo->rank`, and the cached connection is a *self-connection*: a real TCP connection, through the kernel, from a process to a thread of the same process. Cross-rank connections do occur (indirect P2P routes, PXN network offload, NVLS multicast-handle import) but they are topology-selected extras. And the actual baseline is stronger than any transport choice: communicator initialization itself opens one, unconditionally ([init.cc#L1012-L1014](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/init.cc#L1012-L1014)):

```c title="NCCL init.cc @ v2.17.1-1, lines 1012–1014: the connection every init creates"
  // Connect to local net proxy
  NCCLCHECKGOTO(ncclProxyConnect(comm, TRANSPORT_NET, 1, comm->rank, &proxyConn), ret, fail);
  NCCLCHECKGOTO(ncclProxyCallBlocking(&proxyConn, ncclProxyMsgSharedInit, ...), ret, fail);
```

Every rank that has completed `ncclCommInitRank`, whatever its data plane later turns out to be, already holds a cached, persistent TCP self-connection to its own proxy service. The transport selections above then reuse or add to it. One counted connection, guaranteed by init.

<figure class="frame diagram">
  <span class="frame-title">fig. 2 · the minimum viable topology: one rank, one TCP self-connection</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 308" role="img" aria-label="Diagram: inside one rank process, the NCCL Service thread holds the accepted end of a TCP connection and peerSocks[self] holds the client end, opened at communicator init. The connection loops through the kernel's TCP stack on the same host. A forked child created later inherits a duplicate of the client end.">
      <defs>
        <marker id="f2a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--krn)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="24" y="30" width="440" height="180" fill="var(--seg)" opacity="0.08"/>
        <rect x="24" y="30" width="440" height="180" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="38" y="50" fill="var(--seg)">rank process</text>
        <rect x="44" y="64" width="188" height="70" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="56" y="84" fill="var(--text)">"NCCL Service" thread</text>
        <text x="56" y="100" fill="var(--muted)" font-size="10">runs the listener; holds</text>
        <text x="56" y="114" fill="var(--muted)" font-size="10">the ACCEPTED end</text>
        <rect x="280" y="64" width="164" height="70" fill="var(--accent)" opacity="0.10"/>
        <rect x="280" y="64" width="164" height="70" fill="none" stroke="var(--accent)" stroke-width="1.6"/>
        <text x="292" y="84" fill="var(--accent)">peerSocks[self]</text>
        <text x="292" y="100" fill="var(--muted)" font-size="10">CLIENT end · opened at</text>
        <text x="292" y="114" fill="var(--muted)" font-size="10">comm init · cached</text>
        <rect x="24" y="224" width="440" height="56" fill="var(--krn)" opacity="0.10"/>
        <rect x="24" y="224" width="440" height="56" fill="none" stroke="var(--krn)" stroke-width="1.5"/>
        <text x="38" y="270" fill="var(--krn)">kernel · one real TCP connection, host-local</text>
        <polyline points="362,134 362,240 138,240 138,138" fill="none" stroke="var(--krn)" stroke-width="1.5" marker-end="url(#f2a)"/>
        <rect x="500" y="64" width="196" height="86" fill="var(--seg)" opacity="0.08"/>
        <rect x="500" y="64" width="196" height="86" fill="none" stroke="var(--seg)" stroke-width="1.2" stroke-dasharray="5 4"/>
        <text x="512" y="84" fill="var(--seg)">forked child (later)</text>
        <text x="512" y="102" fill="var(--muted)" font-size="10">inherits a duplicate of</text>
        <text x="512" y="116" fill="var(--muted)" font-size="10">the client end — and will</text>
        <text x="512" y="130" fill="var(--muted)" font-size="10">never use it</text>
        <line x1="500" y1="99" x2="444" y2="99" stroke="var(--accent)" stroke-width="1.4" stroke-dasharray="4 3"/>
        <text x="24" y="298" fill="var(--muted)" font-size="10">NVLink carries the tensors; this connection carries setup &amp; teardown</text>
      </g>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--seg)"></span>process</span>
      <span><span class="k" style="background:var(--krn)"></span>kernel / TCP stack</span>
      <span><span class="k" style="background:var(--accent)"></span>the client-end descriptor and its aliases</span>
    </p>
  </div>
</figure>

(One honesty note on the diagram: `fork()` copies the *whole* table — the child also inherits the accepted endpoint, the listener, and every other open NCCL descriptor. The diagram shows only the client-end alias because that is the one whose final release controls whether the service side ever sees EOF.)

This correction makes the bug *more* interesting, not less. You do not need seven peers or even two. You need **one counted connection** — and the common shape is a rank holding both ends of it, with a child process about to matter enormously.

## 4. Teardown in 2.17.1: a protocol narrowed to one exit

`commReclaim` runs teardown in a fixed order ([init.cc:1554–1626](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/init.cc#L1554-L1626)): drain the CUDA streams (`commDestroySync`), close the control connections (`ncclProxyDestroy`), then `commFree` — the `pthread_join` from §2. So everything reduces to: when does the service thread agree to exit? [Its loop answers in its own words](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/proxy.cc#L1391-L1398):

```c title="NCCL proxy.cc @ v2.17.1-1, lines 1391–1398: the exit condition"
  int stop = 0;
  ...
  while (stop == 0 || (stop == 1 && npeers > 0)) {
    /* Even if local comm aborts, we cannot let proxy thread exit if we still have peer
     * connections. Need to wait until all other related comms call abort and safely exit
     * together, or we could face segmentation fault. */
    if (*comm->abortFlag != 0) stop = 1;
    /* never let proxy service thread blocks in poll, or it cannot receive abortFlag. */
    ...
      ret = poll(pollfds, NCCL_MAX_LOCAL_RANKS+1, asyncOpCount ? 0 : 500);
```

The abort flag gets the thread to `stop = 1`, but not out the door. It refuses to exit while `npeers > 0`, and its comment explains why: leave while a client might still send a request and you're a use-after-free. Defensible, even careful.

So `npeers` is the ballgame. Reading [the rest of the loop](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/proxy.cc#L1440-L1502), an accepted connection is retired (`closeConn = 1`, `npeers--`) in four broad classes of event: an explicit `ncclProxyMsgStop`/`ncclProxyMsgClose` message; EOF (`recv()` returning 0 — a peer-visible close arrived); `POLLHUP` or a socket error; or a request failing mid-progress. Messages and closes, politeness and noise.

Now the asymmetry that decides the incident, in [`ncclProxyDestroy`](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/proxy.cc#L1532-L1561):

```c title="NCCL proxy.cc @ v2.17.1-1, abridged: the abort path stops saying goodbye"
    if (*comm->abortFlag == 0) {                 // ← graceful only
      ... connect to own service, send ncclProxyMsgStop ...
    }
    ...
    int type = ncclProxyMsgClose;
    if (*comm->abortFlag == 0)                   // ← graceful only
      NCCLCHECK(ncclSocketSend(state->peerSocks + i, &type, sizeof(int)));
    NCCLCHECK(ncclSocketClose(state->peerSocks + i));   // ← always
```

On graceful destroy, every open control connection receives a polite `Close`, and `npeers` drains on messages alone. On **abort**, both sends are skipped — reasonably: an aborting rank must not block trying to talk to endpoints that may be gone. The connection is simply closed. Which means that on the abort path, the only departure signal the aborting client still *intentionally produces* is the socket-level close itself. (An independent error or an already-delivered command can still retire a connection; nothing else is promised.) The teardown's liveness now rests on one assumption so ordinary it's invisible:

> *"`close()` makes the other side see the connection close."*

In 2.17.1, [`ncclSocketClose`](https://github.com/NVIDIA/nccl/blob/v2.17.1-1/src/misc/socket.cc#L819-L826) is exactly what you'd write without thinking twice:

```c title="NCCL misc/socket.cc @ v2.17.1-1, lines 819–826"
ncclResult_t ncclSocketClose(struct ncclSocket* sock) {
  if (sock != NULL) {
    if (sock->fd >= 0) close(sock->fd);
    ...
```

And that assumption is where the kernel gets a vote.

## 5. What close() actually promises

A file descriptor is an entry in a process's fd table. It refers to an **open file description** — the kernel's shared, reference-counted object — which for a socket ultimately refers to the socket endpoint and its protocol state:

```text title="descriptor → description → endpoint"
   process fd-table entry  ──►  open file description  ──►  socket endpoint
        (per-process)             (shared, refcounted)         (TCP state)
```

[`close(fd)`](https://man7.org/linux/man-pages/man2/close.2.html) removes *one descriptor's reference*. The final release of the underlying socket — the step that performs TCP's connection-close processing and makes anything happen on the wire — occurs only when the **last** descriptor referring to that description is gone. (Exact for this scenario; in full generality the kernel waits for the last *reference* to the description — an in-flight blocked I/O can itself hold one, which is part of why cross-thread `close()` is no cancellation mechanism.) Until then, from the peer's point of view, nothing has happened: the connection is `ESTABLISHED`, `poll()` reports no event, `recv()` would happily block.

Who duplicates descriptors? [`fork()`](https://man7.org/linux/man-pages/man2/fork.2.html) copies the entire table. And the standard hygiene flag — `O_CLOEXEC`/`SOCK_CLOEXEC` — is no defense here: it closes descriptors across `exec()`, but a fork-only child (one that keeps running the parent's image, like a dataloader worker) inherits everything regardless. The 2.17.1 socket paths set no close-on-exec flags (a tree-wide grep finds no `CLOEXEC` at all), leaving these descriptors eligible to survive an `exec()` too unless the launcher explicitly closed them. But the fork-only case is the one no flag would have saved.

<figure class="frame diagram">
  <span class="frame-title">fig. 3 · one socket, two fd tables, one gate on the wire event</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 320" role="img" aria-label="Diagram: the rank process and a forked helper each hold fd 23. Both descriptors point at one shared open file description in the kernel, which refers to the TCP socket. When the rank closes fd 23 the alias count drops from two to one and the connection stays ESTABLISHED with no wire event. Only when the helper exits does the count reach zero and the close-processing run.">
      <defs>
        <marker id="f3a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
        <marker id="f3b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--krn)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="60" y="30" width="230" height="54" fill="var(--seg)" opacity="0.08"/>
        <rect x="60" y="30" width="230" height="54" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="74" y="50" fill="var(--seg)">rank process</text>
        <text x="74" y="68" fill="var(--text)">fd 23</text>
        <rect x="430" y="30" width="230" height="54" fill="var(--seg)" opacity="0.08"/>
        <rect x="430" y="30" width="230" height="54" fill="none" stroke="var(--seg)" stroke-width="1.5" stroke-dasharray="5 4"/>
        <text x="444" y="50" fill="var(--seg)">forked helper</text>
        <text x="444" y="68" fill="var(--text)">fd 23 — the fork()ed duplicate</text>
        <rect x="40" y="120" width="640" height="112" fill="var(--krn)" opacity="0.07"/>
        <rect x="40" y="120" width="640" height="112" fill="none" stroke="var(--krn)" stroke-width="1.5"/>
        <text x="54" y="140" fill="var(--krn)">kernel</text>
        <rect x="170" y="150" width="380" height="30" fill="var(--krn)" opacity="0.12"/>
        <rect x="170" y="150" width="380" height="30" fill="none" stroke="var(--krn)" stroke-width="1.4"/>
        <text x="360" y="169" text-anchor="middle" fill="var(--text)">open file description · descriptor aliases: 2</text>
        <line x1="150" y1="84" x2="300" y2="148" stroke="var(--muted)" stroke-width="1.3" marker-end="url(#f3a)"/>
        <line x1="545" y1="84" x2="430" y2="148" stroke="var(--muted)" stroke-width="1.3" marker-end="url(#f3a)"/>
        <line x1="360" y1="180" x2="360" y2="192" stroke="var(--krn)" stroke-width="1.3" marker-end="url(#f3b)"/>
        <rect x="250" y="194" width="220" height="26" fill="none" stroke="var(--krn)" stroke-width="1.2"/>
        <text x="360" y="211" text-anchor="middle" fill="var(--krn)">TCP socket · client end ⇄ svc end</text>
        <text x="60" y="266" fill="var(--muted)">rank: close(23) → aliases 2→1 → still ESTABLISHED. no wire event.</text>
        <text x="60" y="290" fill="var(--accent)">helper exits    → aliases 1→0 → NOW the close-processing runs.</text>
      </g>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--seg)"></span>process fd tables</span>
      <span><span class="k" style="background:var(--krn)"></span>kernel objects</span>
      <span><span class="k" style="background:var(--accent)"></span>the final release, the only one TCP acts on</span>
    </p>
  </div>
</figure>

Does a training process fork children after that socket exists? In our era's stack, routinely — though the details are version-sensitive, so pin them:

```text title="mechanism prerequisites — and what the incident evidence does not pin"
  NCCL:      2.17.1-class build — close-only ncclSocketClose, verified
             at tag v2.17.1-1
  layout:    one NCCL rank per OS process (the figures assume this
             minimal layout)
  children:  created with fork(). PyTorch DataLoader workers are the
             canonical source (num_workers > 0 — the default is 0, so
             workers exist only if asked for — with the fork context);
             DataLoader mechanics below are pinned to v2.0.1 as a
             representative public source. Incident-era Linux Python
             defaulted to fork; Python 3.14 later moved the POSIX
             default to forkserver.
  ordering:  NCCL communicator initialization completed — which itself
             opens the local self-proxy connection — before the helper
             forked

  not independently recovered from the incident: the exact PyTorch and
  Python builds, which forked helper held the descriptor, and the first
  abort trigger.
```

That ordering line is the load-bearing one, and the layers matter: `init_process_group()` alone may not have created any NCCL communicator — `ProcessGroupNCCL` creates them lazily on first use. But once NCCL communicator *initialization* completes, the connection already exists: §3's unconditional init-time `ncclProxyConnect` ran before init returned. The full required ordering is therefore *NCCL comm init completed < child forked < abort-path close < child releases its alias*. That ordering is easy to satisfy — scripts routinely build DDP and run setup collectives before the first `DataLoader` iterator forks its pool, and non-persistent loaders fork a fresh pool every epoch — but its status here deserves honesty: the mechanism requires it, the code paths make it likely, and the surviving incident evidence does not independently prove it for this run. A worker forked before the socket existed cannot hold it; one forked after holds a duplicate of every control-plane descriptor its parent owned, and will never know it.

## 6. The hang, frame by frame

Now assemble it, in its common direct-P2P shape, which is also its strangest: **a rank deadlocking its own abort, with its own child holding the key.**

<figure class="frame diagram">
  <span class="frame-title">fig. 4 · the self-deadlock, 2.17.1, one rank — minimal one-connection slice</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 380" role="img" aria-label="Timeline diagram with two lanes inside one process. Left lane, the aborting thread: ncclCommAbort runs commReclaim, drains streams, ncclProxyDestroy sends nothing because the abort flag is set, closes peerSocks[self] which only drops the alias count from two to one with no wire event, then commFree blocks in pthread_join on the service thread. Right lane, the NCCL Service thread: sees the abort flag, sets stop to one, but npeers stays one, so it polls every 500 milliseconds forever. Below, the forked helper holds the deciding descriptor alias and appears in no interesting stack trace.">
      <defs>
        <marker id="f4a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <text x="40" y="40" fill="var(--text)">aborting thread (PyTorch error path)</text>
        <line x1="40" y1="48" x2="330" y2="48" stroke="var(--muted)" stroke-width="1"/>
        <text x="400" y="40" fill="var(--text)">"NCCL Service" thread (same process!)</text>
        <line x1="400" y1="48" x2="690" y2="48" stroke="var(--muted)" stroke-width="1"/>
        <text x="40" y="76" fill="var(--text)">ncclCommAbort → commReclaim</text>
        <text x="56" y="100" fill="var(--text)">streams drained</text>
        <text x="56" y="124" fill="var(--text)">ncclProxyDestroy:</text>
        <text x="72" y="148" fill="var(--text)">abortFlag != 0 → send nothing</text>
        <text x="72" y="172" fill="var(--text)">close(peerSocks[self])</text>
        <text x="88" y="196" fill="var(--muted)">aliases 2 → 1 (helper holds one)</text>
        <text x="88" y="220" fill="var(--accent)">→ no wire event</text>
        <text x="56" y="252" fill="var(--text)">commFree:</text>
        <text x="72" y="276" fill="var(--text)">pthread_join(service thread)</text>
        <text x="400" y="76" fill="var(--text)">abortFlag seen → stop = 1</text>
        <text x="400" y="100" fill="var(--text)">npeers = 1 (the self-connection)</text>
        <text x="400" y="124" fill="var(--muted)">poll(500 ms) … nothing</text>
        <text x="400" y="148" fill="var(--muted)">poll(500 ms) … nothing</text>
        <text x="400" y="172" fill="var(--muted)">poll(500 ms) … nothing</text>
        <text x="400" y="196" fill="var(--muted)">poll(500 ms) … nothing</text>
        <text x="400" y="220" fill="var(--accent)">npeers = 1. indefinitely.</text>
        <line x1="248" y1="272" x2="392" y2="228" stroke="var(--accent)" stroke-width="1.5" marker-end="url(#f4a)"/>
        <text x="400" y="252" fill="var(--accent)">blocked until that alias releases —</text>
        <text x="400" y="270" fill="var(--accent)">potentially forever</text>
        <rect x="150" y="310" width="420" height="48" fill="var(--seg)" opacity="0.08"/>
        <rect x="150" y="310" width="420" height="48" fill="none" stroke="var(--seg)" stroke-width="1.2" stroke-dasharray="5 4"/>
        <text x="360" y="330" text-anchor="middle" fill="var(--seg)">forked helper · third process, idle and healthy</text>
        <text x="360" y="347" text-anchor="middle" fill="var(--muted)" font-size="10">holds the deciding alias; appears in no interesting stack trace</text>
        <line x1="252" y1="308" x2="212" y2="228" stroke="var(--accent)" stroke-width="1.2" stroke-dasharray="4 3"/>
      </g>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--seg)"></span>the bystander process</span>
      <span><span class="k" style="background:var(--accent)"></span>the missing event and what waits on it</span>
    </p>
  </div>
</figure>

Two threads of one process, connected to each other through the kernel's TCP stack, deadlocked by the fd table of a *third* process that appears in no interesting stack trace — a forked helper, idle and healthy, incidentally clutching a duplicate of the client end it will never use. In our incident the natural suspects were the dataloader workers; NVIDIA's fix comment (§8) names `fork()` generically, and our surviving evidence doesn't pin which forked helper it was, so treat the worker attribution as reconstruction. The mechanism doesn't care which child it was. Only that one existed.

The cross-rank variant exists too: where topology selected another local rank's proxy (indirect P2P, PXN, NVLS), rank B's withheld close wedges rank *A*'s service thread — one rank's children holding another rank's teardown hostage. And a nuance about process death: a *generic* forked helper can even outlive its parent while pinning the socket (§9 demonstrates exactly that), but stock PyTorch DataLoader workers are daemonic with parent-death machinery ([`w.daemon = True`](https://github.com/pytorch/pytorch/blob/v2.0.1/torch/utils/data/dataloader.py#L1035), [`ManagerWatchdog`](https://github.com/pytorch/pytorch/blob/v2.0.1/torch/utils/data/_utils/worker.py#L51) polling `getppid()`). The production-relevant state is the subtler one: the rank is *alive but hung inside abort*, so its workers see a living parent, keep waiting for batches politely — and keep holding.

## 7. Why it looked random

Once you hold the mechanism, the intermittency stops being spooky. The hang requires a strict ordering across four events, plus a mode:

```text title="the gates"
  proxy socket opened  <  child forked  <  abort-path close()  <  child releases its fd
                                └── and it must be the ABORT path, not graceful destroy
```

Each inequality is a gate:

**Abort, not destroy.** Graceful `ncclCommDestroy` sends `Close` messages; `npeers` drains without any wire event needed. That is why the same NCCL 2.17 sailed through other trainings for months — they ended cleanly. A job whose exits routinely tripped PyTorch's error path took the message-less abort route, night after night.

**A fork after the socket, still unreleased at the close.** `num_workers=0`: no workers, so the DataLoader route is closed (any other `fork()`ed helper can still open it). Workers forked before communicator setup: no gate. A non-persistent worker pool that happened to be torn down between epochs when the abort fired: no gate *tonight*. A worker alive at that millisecond — or any other `fork()`ing helper spawned after setup: gate open. Whether such a child existed at the instant of `close()` is a scheduling accident. That is the whole "not very deterministic."

**Start method.** `fork` hands the child the parent's entire descriptor table; `spawn` and `forkserver` children get only what is deliberately passed to them, not arbitrary inherited descriptors. On incident-era Linux Python, `fork` was the default.

**Multiplier.** Every initialized communicator — and a subgrouped job has many — owns its own service thread and whatever proxy connections its transport setup has lazily created, each an independent instance of the race, with abort *ordering* across communicators adding failure modes of its own (a post-fix sibling: [#1013](https://github.com/NVIDIA/nccl/issues/1013), a single-node, four-rank hang that appears and disappears with the order of two `_abort()` calls).

**An explanation I had to delete.** An earlier draft of this article claimed a rescue gate: unread bytes at `close()` cause TCP to send RST instead of FIN, abruptly waking the peer — so busy teardowns escaped and quiescent ones hung. Tidy, and wrong. The RST-vs-FIN decision happens during the socket's *final-close processing*, and the entire premise of this bug is that the parent's `close()` **is not the final close** — it's an alias decrement that never reaches TCP at all. I tested the adversarial case (unread byte queued, helper holding the fd): the parent's close produced nothing on the peer; only the helper's exit did — as `ECONNRESET`, the unread data changing the *final*-close event, not its timing. The corrected rule: **a non-final `close()` produces no close-related, peer-visible TCP event.**

And the question I also got wrong on the first pass, so it gets its own line: **does this need multiple nodes? No.** The common vulnerable connection is a rank's TCP *self*-connection — it exists on one box, between one process's own threads, and descriptor semantics do not care that both endpoints share a kernel. A single-node NVLink job with a post-setup `fork()` and an abort at exit checks every gate.

## 8. The fix: one syscall in 2.18.1

NCCL 2.18.1's release notes carry a four-word line that is easy to scroll past: *"Fixed hang in commReclaim."* ([2.18.1 fixed issues](https://docs.nvidia.com/deeplearning/nccl/archives/nccl_2181/release-notes/rel_2-18-1.html)). Diff the tags and the relevant fix is confined to one function — same file, [same line 819](https://github.com/NVIDIA/nccl/blob/v2.18.1-1/src/misc/socket.cc#L819-L833) — with NVIDIA's comment on it compressing this entire article into four lines:

```c title="NCCL misc/socket.cc @ v2.18.1-1, lines 819–833: the fix, with the postmortem attached"
ncclResult_t ncclSocketClose(struct ncclSocket* sock) {
  if (sock != NULL) {
    if (sock->fd >= 0) {
      /* shutdown() is needed to send FIN packet to proxy thread; shutdown() is not affected
       * by refcount of fd, but close() is. close() won't close a fd and send FIN packet if
       * the fd is duplicated (e.g. fork()). So shutdown() guarantees the correct and graceful
       * connection close here. */
      shutdown(sock->fd, SHUT_RDWR);
      close(sock->fd);
    }
    ...
```

Why does [`shutdown()`](https://man7.org/linux/man-pages/man2/shutdown.2.html) succeed where `close()` couldn't? It also takes a descriptor — but instead of releasing one alias, it changes the state of the **shared socket** that all the aliases refer to, without waiting for the final descriptor alias. `SHUT_WR` initiates the orderly write-side close the peer can observe; `SHUT_RDWR` disables both directions. The alias count — the mechanism that let a bystander process veto the close — is simply not consulted; the helpers' duplicates still exist afterward and no longer decide anything.

<figure class="frame diagram">
  <span class="frame-title">fig. 5 · the wire, before and after (quiescent connection, as in the reproducer)</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 352" role="img" aria-label="Two panels. Panel one, NCCL 2.17.1 with close only: the rank's close drops the alias count with no wire event, the service polls with npeers stuck, and only the helper's eventual exit, minutes or hours later, delivers the close. Panel two, NCCL 2.18.1 with shutdown then close: shutdown produces an observable close immediately, the service sees recv return zero, npeers drains, the loop exits and pthread_join returns, while the helper is still alive holding a duplicate that no longer decides anything.">
      <defs>
        <marker id="f5a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
        <marker id="f5b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="24" y="26" width="672" height="130" fill="none" stroke="var(--muted)" stroke-width="1.2"/>
        <text x="38" y="48" fill="var(--text)">2.17.1 · close() only — helper holds an alias</text>
        <text x="38" y="76" fill="var(--text)">rank: close(fd)</text>
        <line x1="160" y1="72" x2="330" y2="72" stroke="var(--muted)" stroke-width="1.2" stroke-dasharray="4 3"/>
        <text x="345" y="76" fill="var(--muted)">aliases 2→1 · no wire event · nothing arrives</text>
        <text x="38" y="104" fill="var(--muted)">svc:  poll … poll … poll …  npeers stuck. hang.</text>
        <text x="38" y="136" fill="var(--text)">helper exits (minutes? hours?)</text>
        <line x1="270" y1="132" x2="430" y2="132" stroke="var(--muted)" stroke-width="1.4" marker-end="url(#f5a)"/>
        <text x="442" y="136" fill="var(--muted)">close delivered. svc: finally.</text>
        <rect x="24" y="180" width="672" height="126" fill="var(--accent)" opacity="0.05"/>
        <rect x="24" y="180" width="672" height="126" fill="none" stroke="var(--accent)" stroke-width="1.4"/>
        <text x="38" y="202" fill="var(--text)">2.18.1 · shutdown() then close()</text>
        <text x="38" y="230" fill="var(--text)">rank: shutdown(fd, SHUT_RDWR); close(fd)</text>
        <line x1="310" y1="226" x2="420" y2="226" stroke="var(--accent)" stroke-width="1.6" marker-end="url(#f5b)"/>
        <text x="432" y="230" fill="var(--accent)">observable close, immediately</text>
        <text x="38" y="258" fill="var(--text)">svc:  recv()==0 → npeers-- → loop exits → pthread_join returns ✓</text>
        <text x="38" y="286" fill="var(--muted)" font-size="10">the helper is still alive, still holding its duplicate — it no longer decides anything</text>
        <text x="24" y="326" fill="var(--muted)" font-size="10">with data already queued, the peer reads those bytes first and then sees the EOF;</text>
        <text x="24" y="340" fill="var(--muted)" font-size="10">this connection is quiescent, so EOF is immediate</text>
      </g>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--muted)"></span>close(): the event that may never come</span>
      <span><span class="k" style="background:var(--accent)"></span>shutdown(): the event that always does</span>
    </p>
  </div>
</figure>

Because the fix lives inside `ncclSocketClose`, every connected control socket closed through the wrapper — the abort path's `peerSocks`, the service thread retiring a connection, the bootstrap ring — gains the behavior at once. (The wrapper is also used on listening and not-yet-connected sockets, where `shutdown()` can fail; NCCL ignores its return value, harmlessly.)

Scope the versions honestly: the broken-close behavior is what I verified at v2.17.1-1, the fix at v2.18.1-1; a similar affected-era abort hang was reported on 2.16.5 ([#863](https://github.com/NVIDIA/nccl/issues/863) — it carries no stack or fd evidence, so attributing it to this exact mechanism is plausible rather than proven), and 2.18.1 did *not* end all `ncclCommAbort` hangs: [#992](https://github.com/NVIDIA/nccl/issues/992) and [#1013](https://github.com/NVIDIA/nccl/issues/1013) are post-fix abort hangs with the same silhouette, on builds that already carry the shutdown change — so whatever their (undiagnosed-in-thread) causes, they are not this close-only mechanism. For *this* failure, run a build containing the shutdown-before-close change (vendor backports may carry it without the version number); everything else — reaping stuck ranks externally, `num_workers=0`, non-fork start methods — is mitigation.

## 9. Reproducing it, no GPUs required

Strip away CUDA and the fleet and the incident is three parties and two syscalls — small enough for [~180 lines of C](https://github.com/dshah133/howtf/tree/main/demo/nccl-teardown-fin). One honest disclaimer first: **this is a Linux socket-lifetime reproducer, not an NCCL execution reproducer.** It implements the liveness-critical *reduction* of the 2.17.1 service loop — abort already observed, one connection counted, no application-level Close coming — and proves the duplicated-descriptor delay and the effect of the 2.18.1 change. It does not model the production trigger, the fd holder's identity, or NCCL's real topology. (`svc` and `rank` are also separate processes here purely to isolate the kernel mechanism; §6's common NCCL shape puts both ends in one process.)

```shellsession title="./teardown-hang — abridged representative run (early lines can interleave; routine lines omitted)"
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
```

Read the timestamps: `close()` at **t=0.30**, the rank process dead at t=0.30, the peer notified at **t=3.70** — the moment the helper dropped the last alias. In between, the demo samples the kernel's own table: both loopback endpoints `ESTABLISHED`, more than a second after the closing process ceased to exist. The close was never lost; it was *scheduled by an unrelated process's lifetime*.

```shellsession title="experiment 2, same fork, same two aliases"
== experiment 2 · teardown with shutdown() then close() (NCCL 2.18.1 behavior) ==
rank:   forked helper pid 506 — fd table copied (descriptor aliases: 2)  [t=0.00s]
rank:   teardown: shutdown(fd, SHUT_RDWR); close(fd)   // 2.18.1  [t=0.30s]
svc:    EOF (recv() == 0) — a peer-visible close arrived. npeers-- -> 0  [t=0.30s]
svc:    service loop exits; pthread_join would return; ncclCommAbort completes  [t=0.30s]
        note: the helper is STILL ALIVE, still holding its duplicate
        descriptor. shutdown() acted on the shared socket, not the alias count.
```

Same fork, same two aliases — and the close is peer-visible within the demo's timestamp resolution, while the helper sits there holding a descriptor that no longer decides anything. (Implementation notes for the curious: the demo makes itself a `PR_SET_CHILD_SUBREAPER` so the orphaned helper reparents to it and gets reaped — a forked *grandchild* is otherwise unreapable by the top process, a bug the first version of this demo had; the helper acknowledges over a pipe before teardown proceeds; the mid-hang kernel sampling is built in; timing is `CLOCK_MONOTONIC`; and the two roles are independently scheduled, so early log lines can interleave either way.)

## 10. What generalizes

**`close()` is not a message.** It is a release of one descriptor alias that *sometimes* has wire side effects. If your protocol's liveness depends on the peer observing your departure, perform the observable event explicitly — `shutdown()` before `close()` — and keep timeouts and error paths anyway: no protocol should depend on any peer signal arriving unconditionally.

**Abort paths must not require politeness from the dead — or the living.** The graceful path here had two exits (a message or an observable close); the abort path silently narrowed to one, and nobody re-derived liveness under the narrowing. When you strip an escape path down for safety, re-ask: what is the minimum signal that still gets everyone out, and can any bystander withhold it?

**fd inheritance is part of your interface.** `fork()` couples your subprocess topology to your network protocol's correctness through the descriptor table. `SOCK_CLOEXEC` is necessary hygiene for the exec case and does nothing for fork-only workers — for those, the defenses are start methods that don't inherit, forking before long-lived sockets exist, closing unrelated descriptors in the child, or (best) protocol liveness that doesn't depend on final release at all.

**The unit you operate on is not the unit the kernel acts on.** You closed a *descriptor*; the observable close belonged to the *shared socket* behind it. The same shape as bytes-versus-pages in [the previous NCCL story on this site](/blog/four-bytes-one-page/): the API's noun and the kernel's noun differ by one level of indirection, and the bug lives in the gap.

## Epilogue

Every local decision in this incident was reasonable. The service thread refused to exit while connections stood open, because exiting early is a use-after-free — its comment says so. The abort path skipped the goodbye messages, because an aborting rank must not block on endpoints that may be gone. The helper held the socket because `fork()` hands children the whole table and nobody told them otherwise. And `close()` kept its actual contract — release one alias, close the connection when the aliases are gone — rather than the contract everyone remembered it having.

The contracts did not compose. A teardown whose only abort-path exit was an observable close met a syscall that only sometimes produces one, in a process tree that routinely duplicated the deciding alias into children that would never look at it. Training finished perfectly, every evening. Then, some evenings, the exit door was held shut by an inherited descriptor in a child process that would never use it.

The fix is one syscall and a four-line comment that reads like this post's abstract. And `fork()`, for the second story running, turns out to be the villain — last time it withheld a page from the children; this time a child withheld the close from everyone else. I am starting to keep a file.

---

*The hardware-free reproducer (the reduced 2.17.1 service loop, the forked alias holder, and both teardown variants) lives at [demo/nccl-teardown-fin](https://github.com/dshah133/howtf/tree/main/demo/nccl-teardown-fin) — `gcc -std=gnu11 -O2 -Wall -Wextra -Wpedantic -o teardown-hang teardown-hang.c` and nothing else. Mechanism sources are pinned tags linked inline: NCCL [v2.17.1-1](https://github.com/NVIDIA/nccl/tree/v2.17.1-1) and [v2.18.1-1](https://github.com/NVIDIA/nccl/tree/v2.18.1-1) (and [the diff between them](https://github.com/NVIDIA/nccl/compare/v2.17.1-1...v2.18.1-1)), NVIDIA's 2.18.1 release notes, close(2)/shutdown(2)/fork(2), PyTorch v2.0.1's DataLoader source, and the public reports NVIDIA/nccl#863 (affected era), #992 and #1013 (post-fix).*
