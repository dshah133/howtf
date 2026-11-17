---
title: "howtf did the clock just keep slowing down?"
description: "An ESXi timekeeping story: PTP through a Linux service VM, a pulse-per-second experiment, a 100 ms adjustment cadence, and the unsigned temporary in that refactor that corrupted every negative clock correction."
date: 2026-11-16
updated: 2026-11-17
tags: [esxi, ptp, ntp, timekeeping, linux, virtualization]
---

## 1. The VMs with the problem weren't using PTP

I had been working on precision time support in ESXi. Part of that work changed how promptly the host's clock machinery applied adjustments. Some time after it shipped, reports came in of time errors in VMs after vMotion, including application consistency failures. A short test looked healthy.

We tried to reproduce the migration scenario and got nowhere. What finally worked was much smaller: submit adjustments directly to the host clock. A negative request, one that asked the clock to slow down a little, never finished. The clock kept slowing. No VM had to migrate.

The fix was one line. A temporary variable in a helper I had extracted needed to be signed. The hard part was the route: from an application symptom in a guest, through the guest's dependence on host time, to a shared clock path I had refactored for a feature that guest never used.

> **Scope note.** This is work I did at VMware. No VMware source, logs, or traces appear here. The code is illustrative, and the numbers in the tables come from two small companion models, labeled as models throughout. The fix shipped in ESXi 7.0 Update 3o, and the sample VMware published with it is in section 9. The architecture and the clock mechanisms are linked to public sources inline: VMware's documentation and blog posts, a Photon OS kernel patch that lists me as reviewer, an independent write-up of the shipped ESXi PTP service, the Linux kernel at v6.12, FreeBSD 13.2, and LinuxPTP.

## 2. Why the time service ended up in a VM

The starting point was a virtualization problem. A workload needed an accurate time reference, and a physical NIC could supply the hardware timestamps to build one. Passing that NIC straight through to the workload VM was the direct way to expose the capability. It also tied a physical device to one VM.

For the deployment model we wanted to improve, repeating that for every timing-sensitive VM was an expensive way to distribute time. The goal was to acquire a good reference once, at the host, and hand it to many VMs, which would keep ordinary virtual networking.

PTP itself does not require a whole NIC per VM. Passthrough was the practical route with the hardware and software we had. SR-IOV and shared timestamping designs change the tradeoff, and Broadcom's documentation now lists both DirectPath I/O and SR-IOV options for the ESXi time service ([supported NICs for hardware timestamping](https://knowledge.broadcom.com/external/article/311923/list-of-supported-nics-for-hardwaretimes.html)).

ESXi already had NTP: a port of the reference implementation plus the VMkernel APIs to discipline the system clock ([Timekeeping within ESXi](https://blogs.vmware.com/cloud-foundation/2018/07/11/timekeeping-within-esxi/)). VMware's own blog says PTP support arrived quietly in vSphere 7.0 and had its "grand entrance" in 7.0 Update 2, and the docs date hardware timestamping through a passthrough NIC to 7.0 Update 3 ([Let's be precise](https://blogs.vmware.com/cloud-foundation/2021/04/06/lets-be-precise-enabling-and-configuring-precision-time-protocol-in-vsphere/), [Use PTP for time synchronization of a host](https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere/7-0/vcenter-and-host-management/configuring-hosts-and-vcenter-server-host-management/synchronizing-clocks-on-the-vsphere-network-host-management/editing-time-configuration-for-a-host-host-management/use-ptp-servers-for-time-and-date-synchronization-of-a-host-host-management.html)).

There were two jobs. Synchronize the host to an external reference. Expose a useful reference to the VMs. A guest-facing clock device solves the second job even when the host gets its time from NTP. Adding that device does not make the host a PTP participant.

**A timestamp also needs a clock identity.** Consider what it takes to bring hardware timestamps through a host's ordinary network stack. A receive timestamp taken in the NIC is not the same as reading the clock when the driver processes the completion. On transmit, software hands over the packet before the hardware timestamp exists, so the timestamp has to be matched back to the right packet and delivered to the requester. Linux's timestamping documentation shows the bookkeeping: hardware receive and transmit flags, an identifier per packet, and timestamps returned out of order through the socket error queue ([Linux network timestamping](https://docs.kernel.org/networking/timestamping.html)).

Then there is the question of which clock produced the number. Suppose one logical interface can use either of two physical uplinks. Each NIC has its own PTP hardware clock (PHC), with its own offset and rate. Timestamps from the two are not comparable. An abstraction that deliberately hides the choice of uplink is right for packet delivery and wrong for timing, which needs a stable clock association, an explicit clock identity, or a restriction that keeps timing traffic on one physical path. LinuxPTP enforces the same requirement: `ptp4l` checks that all ports of a boundary clock share one PHC, and its just-a-bunch-of-devices mode requires the clocks to be synchronized by an external program ([ptp4l boundary_clock_jbod](https://www.linuxptp.org/documentation/ptp4l/)).

A fully integrated path meant changes across the driver, virtual networking, and the interfaces that presented timestamps to software. Under our delivery constraints that was a big project. We looked for a way to use an existing PTP stack while keeping the cost at the host level.

ESXi had CRX, the Container Runtime for ESXi. VMware's performance guide describes it as "essentially a lightweight VM" that runs each vSphere pod ([Performance Best Practices for Kubernetes with VMware Tanzu](https://www.vmware.com/docs/vsphere-tanzu-kubernetes-perf), page 26). It gave us a way to run Linux software without first recreating its kernel interfaces inside VMkernel.

The design was a host-managed time service, TRX, in that environment. Assign the timing NIC to the service. Run the Linux driver and LinuxPTP there. Give it a controlled path for the resulting adjustments to reach the host clock. Every other VM consumes the host's reference. An independent write-up of the shipped service describes the same shape: "TRX, ESXi's hardware timestamp PTP implementation, runs in a container on top of VMX and requires passthrough to connect PCI-E devices directly to VMX," and it runs `ptp4l` and `phc2sys` inside ([ESXi PTP deep dive](https://velog.io/@sjuhwan/vSphere-Troubleshooting-ESXi-PTP-Deep-Dive-ENG)).

<figure class="frame diagram">
  <span class="frame-title">fig. 1 · one host, two jobs</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 400" role="img" aria-label="Diagram: on the left, a grandmaster on the network feeds a passed-through timing NIC, which is owned by TRX, a Linux time service running in a CRX VM. TRX steers the ESXi host clock through a provider interface. On the right, the host clock, which NTP can also steer, feeds the Precision Clock device that Linux and Windows guests read.">
      <defs>
        <marker id="c1a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
        <marker id="c1b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--krn)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <text x="40" y="26" fill="var(--text)">acquire a reference</text>
        <text x="420" y="26" fill="var(--text)">distribute host time</text>
        <rect x="40" y="40" width="250" height="40" fill="var(--seg)" opacity="0.10"/>
        <rect x="40" y="40" width="250" height="40" fill="none" stroke="var(--seg)" stroke-width="1.4"/>
        <text x="165" y="58" text-anchor="middle" fill="var(--seg)">grandmaster on the network</text>
        <text x="165" y="72" text-anchor="middle" font-size="10" fill="var(--muted)">PTP over the wire</text>
        <rect x="40" y="112" width="250" height="48" fill="var(--seg)" opacity="0.14"/>
        <rect x="40" y="112" width="250" height="48" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="165" y="131" text-anchor="middle" fill="var(--seg)">timing NIC, passed through</text>
        <text x="165" y="147" text-anchor="middle" font-size="10" fill="var(--muted)">PHC + hardware timestamps</text>
        <rect x="40" y="192" width="250" height="56" fill="var(--ldr)" opacity="0.14"/>
        <rect x="40" y="192" width="250" height="56" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="165" y="212" text-anchor="middle" fill="var(--ldr)">TRX: time service in a CRX VM</text>
        <text x="165" y="229" text-anchor="middle" font-size="10" fill="var(--muted)">Linux NIC driver + ptp4l / phc2sys</text>
        <rect x="420" y="40" width="260" height="40" fill="none" stroke="var(--muted)" stroke-width="1.2" stroke-dasharray="4 3"/>
        <text x="550" y="58" text-anchor="middle" fill="var(--muted)">existing NTP path</text>
        <text x="550" y="72" text-anchor="middle" font-size="10" fill="var(--muted)">alternative source for the host clock</text>
        <rect x="420" y="128" width="260" height="56" fill="var(--krn)" opacity="0.14"/>
        <rect x="420" y="128" width="260" height="56" fill="none" stroke="var(--krn)" stroke-width="1.8"/>
        <text x="550" y="150" text-anchor="middle" fill="var(--krn)">ESXi host clock</text>
        <text x="550" y="167" text-anchor="middle" font-size="10" fill="var(--muted)">shared clock-discipline machinery</text>
        <rect x="420" y="222" width="260" height="48" fill="var(--sec)" opacity="0.10"/>
        <rect x="420" y="222" width="260" height="48" fill="none" stroke="var(--sec)" stroke-width="1.4"/>
        <text x="550" y="241" text-anchor="middle" fill="var(--sec)">Precision Clock device</text>
        <text x="550" y="257" text-anchor="middle" font-size="10" fill="var(--muted)">ACPI VMW0005, read by hypercall</text>
        <rect x="420" y="312" width="120" height="52" fill="var(--sec)" opacity="0.10"/>
        <rect x="420" y="312" width="120" height="52" fill="none" stroke="var(--sec)" stroke-width="1.4"/>
        <text x="480" y="332" text-anchor="middle" fill="var(--sec)">Linux guest</text>
        <text x="480" y="348" text-anchor="middle" font-size="10" fill="var(--muted)">ptp_vmw + chrony</text>
        <rect x="548" y="312" width="140" height="52" fill="var(--sec)" opacity="0.10"/>
        <rect x="548" y="312" width="140" height="52" fill="none" stroke="var(--sec)" stroke-width="1.4"/>
        <text x="618" y="332" text-anchor="middle" fill="var(--sec)">Windows guest</text>
        <text x="618" y="348" text-anchor="middle" font-size="10" fill="var(--muted)">VMware Time Provider</text>
      </g>
      <g stroke="var(--muted)" stroke-width="1.3" fill="none" marker-end="url(#c1a)">
        <path d="M 165 80 L 165 108"/>
        <path d="M 165 160 L 165 188"/>
        <path d="M 550 80 L 550 124"/>
        <path d="M 550 184 L 550 218"/>
        <path d="M 500 270 L 480 308"/>
        <path d="M 600 270 L 618 308"/>
      </g>
      <g stroke="var(--krn)" stroke-width="1.6" fill="none" marker-end="url(#c1b)">
        <path d="M 290 220 L 330 220 L 330 156 L 416 156"/>
      </g>
      <g font-family="var(--font-body)" font-size="10">
        <text x="176" y="98" fill="var(--muted)">PTP exchanges</text>
        <text x="176" y="178" fill="var(--muted)">device access</text>
        <text x="338" y="180" fill="var(--krn)">provider interface</text>
        <text x="338" y="194" fill="var(--muted)">settime · adjtime · adjfreq</text>
        <text x="560" y="98" fill="var(--muted)">or NTP steers it</text>
        <text x="560" y="206" fill="var(--muted)">read-only for guests</text>
      </g>
      <text x="360" y="390" text-anchor="middle" font-family="var(--font-body)" font-size="11" fill="var(--accent)">the provider steers the host clock. every other VM only reads it.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--seg)"></span>hardware clocks</span>
      <span><span class="k" style="background:var(--ldr)"></span>Linux time service</span>
      <span><span class="k" style="background:var(--krn)"></span>ESXi host clock</span>
      <span><span class="k" style="background:var(--sec)"></span>guests</span>
    </p>
  </div>
</figure>

Running Linux processes was not the end of it. A normal guest must not gain authority to steer the host clock because it can read a virtual time device. The service needed a host-controlled image, a privilege boundary, lifecycle management, configuration, and diagnostics, down to making its logging fit host operations.

There is a public artifact for the write side. A Photon OS kernel patch by Vivek Thampi, dated May 2021, adds settime, adjtime, and adjfreq operations to the `ptp_vmw` driver: "On supported versions of VMware precision clock virtual device (hardware version 19 onwards), new commands can adjust its time and frequency, allowing time transfer from a virtual machine to the underlying hypervisor." It carries my Reviewed-by ([Photon patch 0001-ptp-ptp_vmw-Implement-PTP-clock-adjustments-ops](https://github.com/vmware/photon/blob/4.0/SPECS/linux/0001-ptp-ptp_vmw-Implement-PTP-clock-adjustments-ops.patch)). Hardware version 19 is the vSphere 7.0 Update 2 level ([virtual machine hardware versions](https://www.virten.net/vmware/virtual-machine-hardware-versions/)).

## 3. What a workload VM reads

I thought of the distribution side as a virtual time bus: the host maintains a reference, and VMs read it without their own hardware-facing PTP setup. The public guest interface is the part we can inspect.

In Linux v6.12, `drivers/ptp/ptp_vmw.c` binds to the `VMW0005` ACPI device and registers a PTP clock. Its read path is one hypercall that returns the high and low halves of a nanosecond count ([ptp_vmw.c, lines 24-33](https://github.com/torvalds/linux/blob/v6.12/drivers/ptp/ptp_vmw.c#L24-L33)). Its adjtime, adjfine, and settime operations return `-EOPNOTSUPP` ([lines 39-63](https://github.com/torvalds/linux/blob/v6.12/drivers/ptp/ptp_vmw.c#L39-L63)). Upstream, the virtual clock is something a guest reads. The Photon patch above is the provider's write interface, a different contract.

Both interfaces move 64-bit values through unsigned 32-bit register halves, and on the write side the value is signed. That is transport, and it is fine: the Photon adjtime operation splits a signed 64-bit delta into two unsigned words for the hypercall, and the receiver reassembles it. Unsigned words at an interface are not the bug in this story.

The guest also has its own system clock. Reading the virtual reference does not replace it. Guest software has to consume the reference and discipline the system clock. Chrony, for example, can use a PHC device as a reference clock ([chrony refclock PHC](https://chrony-project.org/doc/4.7/chrony.conf.html)). A `/dev/ptpN` device is the Linux clock API, not evidence that the VM owns a NIC. Windows had the VMware Time Provider delivered through VMware Tools ([VMware Time Provider setup](https://blogs.vmware.com/cloud-foundation/2021/04/06/lets-be-precise-enabling-and-configuring-precision-time-protocol-in-vsphere/)).

Guest accuracy depends on the reference, the transfer path, and the guest's own synchronization. Nanosecond units in the interface say nothing about it.

## 4. What a clock adjustment means

A clock can show the wrong time and run at the wrong rate. The two are related, but fixing one does not fix the other.

Let R(t) be reference time, C(t) the local clock, and e(t) = C(t) − R(t) the error. Positive e means the local clock is ahead. Its future depends on rate: the oscillator's own error plus whatever rate correction the synchronization software applies. A +1 ppm correction adds one microsecond per reference second. A negative correction slows the clock relative to its uncorrected rate. The clock keeps advancing throughout.

A phase correction moves the clock's position relative to the reference. Software can step, or it can slew: change the rate temporarily until the phase error is gone. The clock does not have to jump because an application submitted an offset.

For the Linux system clock, the mode matters as much as the field. `ADJ_OFFSET` feeds the kernel's NTP discipline and updates its frequency estimate. `ADJ_OFFSET_SINGLESHOT`, the old `adjtime(3)`, asks for a finite gradual correction in microseconds. `ADJ_SETOFFSET` steps. Frequency is scaled parts per million with 16 fractional bits ([adjtimex(2)](https://man7.org/linux/man-pages/man2/adjtimex.2.html)).

With hardware timestamping, `ptp4l` disciplines the NIC's PHC, and `phc2sys` carries that time to another clock ([phc2sys](https://www.linuxptp.org/documentation/phc2sys/)). Our provider interface added the route into ESXi. Below, "the LinuxPTP time service" means that whole arrangement.

The controller is part of the behavior too. LinuxPTP's PI servo responds to the current phase error and accumulates a frequency estimate, with unlocked, jump, and locked states, configurable gains, and a clamp on the frequency it will command ([pi.c at v4.2](https://github.com/richardcochran/linuxptp/blob/v4.2/pi.c)). A stream of adjustments is the output of that feedback loop.

## 5. Giving the guest clock a physical output

The goal was the guest's system clock synchronized to the grandmaster, with a target of one microsecond. Measuring the service VM's NIC alone would have left the host transfer and the guest's own synchronization outside the experiment.

So the test wired the whole path. TRX kept its assigned NIC as usual. A second timing-capable NIC was passed straight to a Linux workload VM. On that VM, `phc2sys` ran backwards from its common deployment: the guest system clock was the source and the second NIC's PHC was the destination. LinuxPTP supports this directly, `CLOCK_REALTIME` as source and a PHC as sink ([phc2sys source and sink options](https://www.linuxptp.org/documentation/phc2sys/)). The second NIC's hardware one-pulse-per-second output went to the measurement input of the Calnex instrument that was also our grandmaster, a Paragon-neo. The instrument compared the arriving pulse with its own reference ([Paragon-neo](https://calnexsol.com/products/paragon-neo/)).

<figure class="frame diagram">
  <span class="frame-title">fig. 2 · export guest time as a hardware pulse</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 300" role="img" aria-label="Diagram of the measurement loop: the Calnex reference feeds TRX and NIC A over PTP, TRX steers the ESXi host clock through the provider interface, the guest system clock synchronizes from the host, phc2sys drives NIC B's hardware clock from the guest system clock, and NIC B's one-pulse-per-second output returns to the Calnex measurement input, which compares it against the same reference.">
      <defs>
        <marker id="c2a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
        <marker id="c2b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--accent)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="40" y="52" width="160" height="52" fill="var(--seg)" opacity="0.14"/>
        <rect x="40" y="52" width="160" height="52" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="120" y="73" text-anchor="middle" fill="var(--seg)">Calnex reference</text>
        <text x="120" y="90" text-anchor="middle" font-size="10" fill="var(--muted)">grandmaster timing</text>
        <rect x="250" y="52" width="180" height="52" fill="var(--ldr)" opacity="0.14"/>
        <rect x="250" y="52" width="180" height="52" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="340" y="73" text-anchor="middle" fill="var(--ldr)">TRX + NIC A</text>
        <text x="340" y="90" text-anchor="middle" font-size="10" fill="var(--muted)">ptp4l acquires time</text>
        <rect x="480" y="52" width="200" height="52" fill="var(--krn)" opacity="0.14"/>
        <rect x="480" y="52" width="200" height="52" fill="none" stroke="var(--krn)" stroke-width="1.8"/>
        <text x="580" y="73" text-anchor="middle" fill="var(--krn)">ESXi host clock</text>
        <text x="580" y="90" text-anchor="middle" font-size="10" fill="var(--muted)">provider applies adjustments</text>
        <rect x="480" y="196" width="200" height="52" fill="var(--sec)" opacity="0.10"/>
        <rect x="480" y="196" width="200" height="52" fill="none" stroke="var(--sec)" stroke-width="1.5"/>
        <text x="580" y="217" text-anchor="middle" fill="var(--sec)">guest system clock</text>
        <text x="580" y="234" text-anchor="middle" font-size="10" fill="var(--muted)">consumes the virtual reference</text>
        <rect x="250" y="196" width="180" height="52" fill="var(--seg)" opacity="0.14"/>
        <rect x="250" y="196" width="180" height="52" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="340" y="217" text-anchor="middle" fill="var(--seg)">NIC B PHC</text>
        <text x="340" y="234" text-anchor="middle" font-size="10" fill="var(--muted)">follows the guest clock</text>
        <rect x="40" y="196" width="160" height="52" fill="var(--seg)" opacity="0.14"/>
        <rect x="40" y="196" width="160" height="52" fill="none" stroke="var(--seg)" stroke-width="1.5"/>
        <text x="120" y="217" text-anchor="middle" fill="var(--seg)">Calnex measurement</text>
        <text x="120" y="234" text-anchor="middle" font-size="10" fill="var(--muted)">pulse vs reference</text>
      </g>
      <g stroke="var(--muted)" stroke-width="1.4" fill="none" marker-end="url(#c2a)">
        <path d="M 200 78 L 246 78"/>
        <path d="M 430 78 L 476 78"/>
        <path d="M 580 104 L 580 192"/>
        <path d="M 480 222 L 434 222"/>
      </g>
      <g stroke="var(--accent)" stroke-width="1.6" fill="none" marker-end="url(#c2b)">
        <path d="M 250 222 L 204 222"/>
      </g>
      <line x1="120" y1="104" x2="120" y2="192" stroke="var(--muted)" stroke-width="1.2" stroke-dasharray="4 4"/>
      <g font-family="var(--font-body)" font-size="10">
        <text x="223" y="70" text-anchor="middle" fill="var(--muted)">PTP</text>
        <text x="453" y="70" text-anchor="middle" fill="var(--muted)">provider</text>
        <text x="588" y="150" fill="var(--muted)">guest sync</text>
        <text x="455" y="188" text-anchor="middle" fill="var(--muted)">phc2sys, reversed</text>
        <text x="227" y="212" text-anchor="middle" fill="var(--accent)">1 PPS</text>
        <text x="128" y="150" fill="var(--muted)">same instrument</text>
      </g>
      <text x="360" y="286" text-anchor="middle" font-family="var(--font-body)" font-size="11" fill="var(--accent)">NIC B is measurement apparatus. its PHC follows the guest, not the grandmaster.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--seg)"></span>hardware clocks and instrument</span>
      <span><span class="k" style="background:var(--ldr)"></span>Linux time service</span>
      <span><span class="k" style="background:var(--krn)"></span>ESXi host clock</span>
      <span><span class="k" style="background:var(--sec)"></span>guest</span>
      <span><span class="k" style="background:var(--accent)"></span>the observable</span>
    </p>
  </div>
</figure>

The direction is the point. The guest got its time through the infrastructure under test. NIC B turned that guest time into an externally observable signal. NIC B's PHC had to follow the guest clock and nothing else. A second servo synchronizing NIC B straight to the grandmaster would have measured the wrong thing, even though the NIC sat on the same network.

There is an error budget on the output side. The PHC lags or differs from the guest clock it follows. Pulse generation, the cable, and the instrument add their own contributions. With a consistent sign convention, the reported error is the guest error plus those transfer and measurement errors, and they can cancel. A strict guest-clock bound needs them characterized. PPS also leaves whole-second ambiguity (phc2sys's own PPS mode uses the source clock only to fix whole seconds), and a pulse per second does not show excursions between pulses ([Linux PHC output facilities](https://docs.kernel.org/driver-api/ptp.html), [phc2sys PPS mode](https://www.linuxptp.org/documentation/phc2sys/)).

The experiment still gave us the complete path. The measured error would start inside the target range, swing outside it, return, and leave again. After we changed the host's adjustment cadence the excursions became much smaller and the runs stayed within target.

## 6. Why applying a correction sooner helped

Our diagnosis was that the host machinery could keep steering with an old correction after the time service had requested a new one. The host recomputed and applied its pending adjustment once per second. That was too coarse for the response our loop needed, and we moved it to 100 ms. This was the cadence of clock-discipline work, not the resolution at which software could read time.

A clock can be exactly on time at one instant and still be running too fast. If a controller has been accelerating a lagging clock, it needs that acceleration reduced as the clock approaches the reference. Holding the old steering for too long carries the clock past the reference. A delayed correction in the opposite direction carries it back past. Delay eats a feedback loop's stability margin and makes its oscillations harder to damp ([control systems with delays](https://www.mathworks.com/help/control/ug/analyzing-control-systems-with-delays.html)). That fits the repeated excursions we saw, and the improvement after the change supports it, though it does not isolate every servo's contribution along the path.

A small numerical example shows the cost of stale steering without pretending to reproduce the servo. Suppose the requested rate correction starts at +1 ppm and switches to −1 ppm at 235 ms. Integrate its contribution over one reference second. A backend either applies every request immediately or copies the latest requested rate at periodic callbacks.

| Backend | Integrated correction after 1 s | Difference from immediate |
| --- | ---: | ---: |
| immediate application | −0.530 µs | 0 |
| 1 s callbacks | +1.000 µs | +1.530 µs |
| 100 ms callbacks | −0.400 µs | +0.130 µs |
| 10 ms callbacks | −0.520 µs | +0.010 µs |

<figure class="frame diagram">
  <span class="frame-title">fig. 3 · a held setpoint is not a dropped one</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 285" role="img" aria-label="Two rate timelines over one second. The requested rate is +1 ppm and switches to −1 ppm at 235 ms. With immediate application the switch happens at 235 ms and the integral after one second is −0.530 microseconds. With 100 ms callbacks the old +1 ppm rate is held until 300 ms, a 65 ms band highlighted in accent, and the integral is −0.400 microseconds.">
      <g font-family="var(--font-mono)" font-size="11">
        <text x="40" y="30" fill="var(--text)">requested rate: +1 ppm, then −1 ppm at 235 ms</text>
        <text x="40" y="70" font-size="10" fill="var(--muted)">applied immediately</text>
        <text x="400" y="70" font-size="10" fill="var(--muted)">integral after 1 s: −0.530 µs</text>
        <line x1="80" y1="100" x2="680" y2="100" stroke="var(--border)"/>
        <rect x="80" y="82" width="141" height="18" fill="var(--ldr)" opacity="0.4"/>
        <rect x="221" y="100" width="459" height="18" fill="var(--krn)" opacity="0.4"/>
        <text x="688" y="96" font-size="10" fill="var(--ldr)">+1</text>
        <text x="688" y="115" font-size="10" fill="var(--krn)">−1</text>
        <text x="40" y="170" font-size="10" fill="var(--muted)">copied at 100 ms callbacks</text>
        <text x="400" y="170" font-size="10" fill="var(--muted)">integral after 1 s: −0.400 µs</text>
        <line x1="80" y1="200" x2="680" y2="200" stroke="var(--border)"/>
        <rect x="80" y="182" width="141" height="18" fill="var(--ldr)" opacity="0.4"/>
        <rect x="221" y="182" width="39" height="18" fill="var(--accent)" opacity="0.7"/>
        <rect x="260" y="200" width="420" height="18" fill="var(--krn)" opacity="0.4"/>
        <text x="688" y="196" font-size="10" fill="var(--ldr)">+1</text>
        <text x="688" y="215" font-size="10" fill="var(--krn)">−1</text>
        <g stroke="var(--muted)" stroke-width="1">
          <line x1="80" y1="218" x2="80" y2="224"/>
          <line x1="221" y1="218" x2="221" y2="224"/>
          <line x1="260" y1="218" x2="260" y2="224"/>
          <line x1="680" y1="218" x2="680" y2="224"/>
        </g>
        <g font-size="10" fill="var(--muted)" text-anchor="middle">
          <text x="80" y="236">0</text>
          <text x="214" y="236">235</text>
          <text x="266" y="236">300</text>
          <text x="680" y="236">1000 ms</text>
        </g>
        <text x="221" y="256" font-size="10" fill="var(--accent)">65 ms of stale +1 ppm: 2 ppm × 65 ms = +0.130 µs of error</text>
      </g>
      <text x="360" y="276" text-anchor="middle" font-family="var(--font-body)" font-size="11" fill="var(--accent)">nothing was dropped. the old setpoint was simply used for too long.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--ldr)"></span>+1 ppm applied</span>
      <span><span class="k" style="background:var(--krn)"></span>−1 ppm applied</span>
      <span><span class="k" style="background:var(--accent)"></span>stale steering</span>
    </p>
  </div>
</figure>

At 100 ms intervals the new command becomes active at 300 ms, 65 ms after it was requested. The difference between the two commands is 2 ppm, and holding the old one for 65 ms contributes 0.130 µs of error. The companion's `update_cadence.py` computes all four rows with exact rational arithmetic. There is no feedback controller in it, so it cannot show oscillation, convergence, or achieved ESXi accuracy. It shows what a held setpoint costs.

A one-second interval is not inherently incompatible with precise timing. A correctly rated clock advances accurately between updates, and both `ptp4l`'s default Sync interval and `phc2sys`'s default update rate are one second, with options to change them ([ptp4l logSyncInterval](https://www.linuxptp.org/documentation/ptp4l/), [phc2sys -R](https://www.linuxptp.org/documentation/phc2sys/)). The issue was the response of our particular path and configuration.

Linux's own NTP discipline is the useful comparison, and it separates the two cadences cleanly. The phase side is bookkept once per second: `second_overflow()` recomputes the chunk of phase correction for the coming second and folds it into the tick length ([ntp.c, lines 464-469](https://github.com/torvalds/linux/blob/v6.12/kernel/time/ntp.c#L464-L469)). That tick length is the length of one NTP interval, which Linux defines as one tick ([timex.h, line 159](https://github.com/torvalds/linux/blob/v6.12/include/linux/timex.h#L159)). The timekeeper steers its clocksource multiplier toward it on every update ([timekeeping.c, lines 2131-2146](https://github.com/torvalds/linux/blob/v6.12/kernel/time/timekeeping.c#L2131-L2146)), so the correction is spread through the second and never lands in one lump. A frequency change does not even wait for the second boundary. The comment in `ntp_update_frequency()` says it outright: "Don't wait for the next second_overflow, apply the change to the tick length immediately" ([ntp.c, lines 259-279](https://github.com/torvalds/linux/blob/v6.12/kernel/time/ntp.c#L259-L279)). That is the distinction between a correction request, its application, and the continuing clock. It says nothing about ESXi's constants. Ours were one second before the change and 100 ms after it.

"Adjustments were lost" would be the wrong description of what we fixed. A frequency command is a replacement setpoint: use this rate until the next command. A delayed setpoint produces the wrong integrated correction even when no queue drops anything.

The cadence work needed a refactor of machinery shared with the existing NTP path. So a change made for PTP could break NTP.

## 7. Following the error back from the VM

The report came through the guest. Its clock was showing an accumulating error, and it synchronized time from the host, through VMware Tools. Following that dependency is what took the investigation to the host clock.

Keep this separate from the new Precision Clock path. The affected workload was not using PTP. It inherited a host timekeeping defect through ordinary host-to-guest synchronization.

vMotion made the initial picture harder to read. It was a mixed-version deployment, a source host on 7.0 U2 and a destination on 7.0 U3. That placed the symptom near several plausible boundaries: migration, guest synchronization, and different host code. The PTP work had shipped with the newer release. Migration's real role turned out to be moving the VM onto a host with the new code.

Tools also syncs a guest once after a vMotion, but that sync only moves a guest forward, so it could not hand over a slow host's time ([KB 326306](https://knowledge.broadcom.com/external/article/326306/disabling-time-synchronization-for-virtu.html)). Periodic sync could: when a guest is ahead, Tools runs its clock more slowly until the two match ([periodic time synchronization](https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/tools/12-5-0/vmware-tools-administration-12-5-0/configuring-vmware-tools-components/using-vmware-tools-configuration-utility/enable-periodic-time-synchronization.html)).

We initially tried to reproduce the larger scenario. Lining up the circumstances was hard, and waiting for the error to build up made each attempt expensive. There was no unmistakable jump to trigger on.

I started fuzzing adjustments submitted directly to the host. For each request, I accumulated the corrections the NTP clock machinery actually applied and watched how they evolved. Negative requests stood out: the accumulated correction came out unexpectedly large. It was not an absolute accuracy measurement, and it did not need to be. It pointed at the adjustment path, and it needed no vMotion.

**Why only some hosts.** The host clock was the thing that drifted. That should have been everywhere, and it wasn't. Three conditions narrowed it. Not every customer had moved to 7.0 U3. Periodic host-to-guest synchronization in VMware Tools is off by default ([disabling time synchronization for virtual machines](https://knowledge.broadcom.com/external/article/326306/disabling-time-synchronization-for-virtu.html)), so most VMs never followed the host clock continuously. And a steady drift is hard to see until a workload compares its time with the outside world. This customer's application did.

A fourth condition decided which hosts drifted: the host had lost synchronization with its NTP server. VMware's release note for the fix says the drift appears "after synchronization with the NTP server fails" ([ESXi 7.0 Update 3o release notes](https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere/7-0/release-notes/esxi-update-and-patch-release-notes/vsphere-esxi-70u3o-release-notes.html), PR 3246132).

## 8. A signed parameter was not enough

Before the refactor, the path used a signed adjustment value directly. I extracted work into a helper whose parameter was still correctly signed. Inside the helper, I introduced an unsigned temporary.

That is a less conspicuous change than replacing an existing signed declaration with an unsigned one. The caller and the function signature kept communicating the right contract. A new working copy broke it.

Here is the shape of the error. The quantity is a pending clock correction in microseconds, and the helper records how much of it was just applied:

```c title="the shape of the error, illustrative, not ESXi source"
static void account_broken(int64_t *remaining_us, int32_t step_us)
{
    uint32_t temporary = step_us;      /* the refactor's new working copy */
    *remaining_us -= temporary;
}

static void account_fixed(int64_t *remaining_us, int32_t step_us)
{
    int32_t temporary = step_us;       /* the one-line fix */
    *remaining_us -= temporary;
}
```

The widening is essential. The subtraction is done in 64 bits, and every 32-bit unsigned value fits in 64-bit signed arithmetic, so the original negative value is not recovered. C defines the conversion to unsigned modulo 2^32 ([C11 draft, section 6.3.1.3](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)). A step of −5,000 µs becomes 4,294,962,296 in the broken helper. The pending amount should move 5,000 µs closer to zero. It moves about 4,295 seconds further away. No signed overflow and no memory corruption is needed.

A positive step survives the copy unchanged. Only negative slew steps broke.

## 9. How a huge internal value makes a slow clock

A value that size, put straight into the clock, would be a huge step. The reports described a steady drift.

The reason is where the value went. It went into a pending slew amount. The kernel keeps a signed amount still to apply, changes the clock rate enough to work off part of it, and reduces the remainder over time. A rate limit makes the clock's visible behavior look restrained even when the pending amount is absurd.

Linux does exactly this in its single-shot path. `time_adjust` holds the remaining correction ([ntp.c, line 80](https://github.com/torvalds/linux/blob/v6.12/kernel/time/ntp.c#L80)), and `second_overflow()` consumes at most `MAX_TICKADJ`, 500 microseconds, per second:

```c title="Linux kernel/time/ntp.c @ v6.12, lines 474-491: second_overflow() works off a single-shot slew"
	if (!time_adjust)
		goto out;

	if (time_adjust > MAX_TICKADJ) {
		time_adjust -= MAX_TICKADJ;
		tick_length += MAX_TICKADJ_SCALED;
		goto out;
	}

	if (time_adjust < -MAX_TICKADJ) {
		time_adjust += MAX_TICKADJ;
		tick_length -= MAX_TICKADJ_SCALED;
		goto out;
	}

	tick_length += (s64)(time_adjust * NSEC_PER_USEC / NTP_INTERVAL_FREQ)
							 << NTP_SCALE_SHIFT;
	time_adjust = 0;
```

The kernel also does not range-check the single-shot amount. `timekeeping_validate_timex()` checks capability, the tick range, the fields of a step, and frequency overflow, and nothing about the `ADJ_ADJTIME` offset ([timekeeping.c, lines 2471-2531](https://github.com/torvalds/linux/blob/v6.12/kernel/time/timekeeping.c#L2471-L2531)). `__do_adjtimex()` stores it as given ([ntp.c, lines 775-781](https://github.com/torvalds/linux/blob/v6.12/kernel/time/ntp.c#L775-L781)). glibc's `adjtime()` wrapper refuses anything beyond about 2,145 seconds ([glibc adjtime.c](https://sourceware.org/git/?p=glibc.git;a=blob;f=sysdeps/unix/sysv/linux/adjtime.c;hb=glibc-2.40)), but nothing below the wrapper does. A 4,295-second single-shot request through the raw syscall is accepted on a 64-bit kernel and, if nothing replaces it, worked off at 500 microseconds per second for 99 days. An absurd pending amount is not something the kernel questions.

The host works a pending correction off under a rate rule. I won't quote ESXi source. FreeBSD's kernel has a public rule of the same shape, and the model uses it: a pending `adjtime()` correction is worked off at 5,000 µs a second while more than a second is pending, at 500 µs a second below that, and the last 500 µs or less goes in one step ([kern_ntptime.c, lines 604-621](https://github.com/freebsd/freebsd-src/blob/release/13.2.0/sys/kern/kern_ntptime.c#L604-L621)).

The companion C program runs that rule with the two helpers from section 8. There is no daemon and no subsequent request. For a request of −300 µs:

| Second | Fixed: step | Fixed: pending after | Broken: step | Broken: pending after |
| ---: | ---: | ---: | ---: | ---: |
| 1 | −300 µs | 0 | −300 µs | −4,294,967,296 µs |
| 2 | 0 | 0 | −5,000 µs | −8,589,929,592 µs |
| 72 | 0 | 0 | −5,000 µs | −309,237,290,312 µs |
| 3,600 | 0 | 0 | −5,000 µs | −15,461,864,270,600 µs |

The fixed model finishes its 300 µs slew in the first second. The broken model applies the same step, then subtracts 2^32 − 300 from the pending amount and is left with exactly −2^32. That is more than a second pending, so every later second slows the clock by another 5,000 µs and pushes the pending amount about 4,295 seconds further out. Any request from −1 to −500 µs lands on the same −2^32 after its first step. Second 72 is in the table for a reason.

VMware published a sample of this state with the fix. The release note and its knowledge-base article print the NTP clock data from an affected host ([ESXi 7.0 Update 3o release notes](https://techdocs.broadcom.com/us/en/vmware-cis/vsphere/vsphere/7-0/release-notes/esxi-update-and-patch-release-notes/vsphere-esxi-70u3o-release-notes.html), PR 3246132, and [KB 312016](https://knowledge.broadcom.com/external/article/312016/system-clock-drifts-on-esxi-70-u3-or-lat.html)):

```text title="vsish -e get /system/ntpclock/clockData, from KB 312016, abridged"
Poll shift:6
Time offset (nsec):-143603
Freq offset (nsec/sec):12888
adjtime() (usec):-309237290312
tick adjustment (nsec/sec):-4987251
```

The `adjtime()` field is the pending amount. The host still owes itself −309,237 seconds, about three and a half days. That is row 72 of the table, to the microsecond: −309,237,290,312 is −72 × 2^32 + 71 × 5,000, which is 72 wraps of a 32-bit value and 71 steps of 5,000 µs. The fields are the state variables FreeBSD's routine keeps ([lines 150-163](https://github.com/freebsd/freebsd-src/blob/release/13.2.0/sys/kern/kern_ntptime.c#L150-L163)).

The cadence change made the host apply its correction every 100 ms. The pending amount was still settled once a second, which is why the steps in the sample are whole-second amounts.

The tick adjustment in the sample agrees. Frequency contributes 12,888 ns/s. The phase offset contributes about −140, which is −143,603 shifted right by 10 (a loop gain of 4 plus the poll shift of 6). The slew contributes −5,000,000. That adds up to −4,987,252 against a printed −4,987,251. The clock was being slowed by 4,987 µs every second. That is about 5,000 ppm, or 18 seconds an hour.

<figure class="frame diagram">
  <span class="frame-title">fig. 4 · a sign lost under a rate cap (model)</span>
  <div class="diagram-body">
    <svg viewBox="0 0 720 300" role="img" aria-label="Left: in the first second a signed step of minus 300 microseconds passes through an unsigned 32-bit temporary and becomes 4,294,966,996, which is subtracted from the pending amount and leaves minus 4,294,967,296. Right: a schematic plot of the pending correction against time. The fixed path reaches zero after one second and stays there. The broken path falls by about two to the power 32 microseconds every second and reaches minus 309,237,290,312 at second 72.">
      <defs>
        <marker id="c4a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--muted)"/>
        </marker>
      </defs>
      <g font-family="var(--font-mono)" font-size="11">
        <rect x="40" y="48" width="250" height="36" fill="var(--ldr)" opacity="0.14"/>
        <rect x="40" y="48" width="250" height="36" fill="none" stroke="var(--ldr)" stroke-width="1.5"/>
        <text x="165" y="70" text-anchor="middle" fill="var(--ldr)">step: −300 µs (int32_t)</text>
        <rect x="40" y="128" width="250" height="36" fill="var(--accent)" opacity="0.14"/>
        <rect x="40" y="128" width="250" height="36" fill="none" stroke="var(--accent)" stroke-width="1.8"/>
        <text x="165" y="150" text-anchor="middle" fill="var(--accent)">temporary: 4,294,966,996</text>
        <rect x="40" y="208" width="250" height="36" fill="var(--krn)" opacity="0.14"/>
        <rect x="40" y="208" width="250" height="36" fill="none" stroke="var(--krn)" stroke-width="1.5"/>
        <text x="165" y="230" text-anchor="middle" fill="var(--krn)">pending: −4,294,967,296 µs</text>
        <text x="360" y="52" fill="var(--text)">pending correction over time (not to scale)</text>
        <line x1="360" y1="84" x2="670" y2="84" stroke="var(--border)"/>
        <line x1="360" y1="66" x2="360" y2="240" stroke="var(--border)"/>
        <path d="M 360 90 L 372 84 L 670 84" fill="none" stroke="var(--ldr)" stroke-width="2"/>
        <path d="M 360 90 L 670 236" fill="none" stroke="var(--accent)" stroke-width="2"/>
        <text x="420" y="76" font-size="10" fill="var(--ldr)">fixed: zero after one second</text>
        <text x="372" y="202" font-size="10" fill="var(--accent)">broken: about 2^32 µs more</text>
        <text x="372" y="216" font-size="10" fill="var(--accent)">owed every second</text>
        <text x="372" y="230" font-size="10" fill="var(--accent)">second 72: −309,237,290,312</text>
        <text x="670" y="100" font-size="10" fill="var(--muted)" text-anchor="end">time</text>
        <text x="352" y="88" font-size="10" fill="var(--muted)" text-anchor="end">0</text>
      </g>
      <g stroke="var(--muted)" stroke-width="1.3" fill="none" marker-end="url(#c4a)">
        <path d="M 155 84 L 155 124"/>
        <path d="M 155 164 L 155 204"/>
      </g>
      <g font-family="var(--font-body)" font-size="10">
        <text x="163" y="108" fill="var(--accent)">uint32_t temporary = step_us;</text>
        <text x="163" y="188" fill="var(--muted)">pending −= temporary (was −300)</text>
        <text x="40" y="268" fill="var(--muted)">the clock still gets a signed step each second, inside the rate limit.</text>
      </g>
      <text x="360" y="292" text-anchor="middle" font-family="var(--font-body)" font-size="11" fill="var(--accent)">a lost sign made the amount enormous. a rate cap kept the symptom steady.</text>
    </svg>
    <p class="legend">
      <span><span class="k" style="background:var(--ldr)"></span>signed, correct</span>
      <span><span class="k" style="background:var(--accent)"></span>after the conversion</span>
      <span><span class="k" style="background:var(--krn)"></span>clock machinery state</span>
    </p>
  </div>
</figure>

The rate is not the bug's number. It is a clamp, and clock discipline clamps at round numbers: Linux's maximum frequency error is 500,000 ns/s, its single-shot slew cap is 500 µs/s, and `adjtimex(2)` clamps a requested frequency to ±32,768,000 in scaled units, which is 500 ppm ([timex.h, line 136](https://github.com/torvalds/linux/blob/v6.12/include/linux/timex.h#L136), [adjtimex(2)](https://man7.org/linux/man-pages/man2/adjtimex.2.html)). FreeBSD's `adjtime` path uses the same 500 and goes to ten times that once more than a second is pending. A corrupted amount that hits a cap comes out at the cap.

Apart from the five published lines, the numbers here come from the model. A running daemon submits more requests, and each one can replace, extend, or reset a pending correction. The model leaves that out on purpose. It shows how a large corrupted amount and a steady rate error coexist.

The remaining question is why no limit caught it. The request was small and valid, so a check at the entry point passes it. The unsigned conversion happens afterward, inside the bookkeeping. The rate limit bounds how quickly the correction is applied. It does not look at the pending amount. A check after each step would: a step has to move the pending amount toward zero and never past it. The companion tests that, and the broken helper fails it on the first negative step.

The PPS runs never showed this, and that is consistent with how LinuxPTP drives a clock. It steers a locked clock by frequency and steps a large offset ([phc2sys.c, lines 602-616](https://github.com/richardcochran/linuxptp/blob/v4.2/phc2sys.c#L602-L616)). Neither leaves a pending slew.

Two things to keep straight. Ordinary `ADJ_OFFSET` is not interchangeable with this finite slew, because it also moves NTP's frequency estimate, so summing every adjustment and expecting one submitted offset needs justification from the mode and the existing state. And unsigned words are sometimes exactly right at an interface, as in the Photon patch's register halves. The error is treating a converted negative value as a positive arithmetic quantity inside the adjustment machinery.

## 10. Making the explanation testable

The fix was the declaration. The companion makes the mechanism runnable. Both programs operate on simulated numeric state and never touch an operating-system clock ([demo/clock-signed-slew](https://github.com/dshah133/howtf/tree/main/demo/clock-signed-slew)):

```sh title="run the companion models"
cc -std=c11 -O2 -Wall -Wextra -Wconversion -Wsign-conversion signed_clock.c -o signed_clock
./signed_clock
python3 update_cadence.py
```

```text title="captured output, abridged (... marks omitted lines)"
step_us   broken_subtracts   fixed_subtracts
  -5000         4294962296             -5000
     -1         4294967295                -1
      0                  0                 0
   5000               5000              5000
...
second   broken_step_us   broken_pending_us   fixed_step_us   fixed_pending_us
     1             -300         -4294967296            -300                  0
     2            -5000         -8589929592               0                  0
    72            -5000       -309237290312               0                  0
  3600            -5000     -15461864270600               0                  0
...
Published sample, ESXi 7.0 U3o release note (PR 3246132):
  adjtime() (usec)            : -309237290312
  -72 * 2^32 + 71 * 5000      : -309237290312
  model, 72 s, broken helper  : -309237290312  (all 500 requests from -500 to -1 us)
...
PASS: 7 signed cases; 26 trajectory cases; 35 cadence cases; published value reproduced; conservation; step invariant; closed-form oracle.

immediate       : integrated =  -0.530 us; difference =  +0.000 us
1 s callback    : integrated =  +1.000 us; difference =  +1.530 us
100 ms callback : integrated =  -0.400 us; difference =  +0.130 us
10 ms callback  : integrated =  -0.520 us; difference =  +0.010 us
PASS: four analytic areas and event-aligned control case.
```

The C program checks zero, the smallest positive and negative steps in its units, and the rule's limits on both sides of zero. It verifies that the fixed helper preserves the step, that negative steps expose the broken conversion, and that a check after each step catches the corruption. It checks conservation on the fixed path, applied plus pending equals the request. And it requires the broken path to reach the published value after exactly 72 steps, from any request between −500 and −1 µs.

Changing cadence deserves its own check. The model splits one second's step across 1, 7, 10, 100, and 1,000 callbacks, including counts that do not divide it evenly, and requires the pieces to add up to the same step. Running ten times as often must not multiply a fixed correction rate by ten.

The compiler emits a sign-conversion warning at the intentional bug. `-fsanitize=undefined` does not report it, because the conversion is defined by C. Clang has a separate opt-in check for it, `-fsanitize=implicit-integer-sign-change`. Diagnostics help, and the invariant outlives any one tool: a signed correction has to keep its meaning through every working copy and unit conversion.

A full-system regression would replay the negative-input host case, ordinary NTP operation, guest synchronization, and the PTP and PPS measurement path, with the faster cadence enabled during the signedness comparison so that the type fix is checked while the original performance change is preserved. Migration remains a useful integration scenario because it was part of the report, but it belongs outside the minimal arithmetic test.

## 11. What generalizes

**A refactor can change meaning without changing a signature.** The parameter stayed signed. The caller stayed correct. A working copy inside the helper carried a different type, and review saw plumbing. Every conversion is a place where a value's meaning can be lost, including the ones that look like copies.

**A rate limit hides a wrong amount.** Bounded consumption made an absurd pending correction look like a steady drift. The limit was doing its job. It was never a validation. Check the amount after every conversion, not only at the entry point.

**When a value hits a cap, the cap is what you observe.** The round rate was not the bug's number. It was the machinery's limit, and the corrupted value pinned it there. A suspiciously round symptom is a hint to look for the clamp, then ask what was on the other side of it.

**Cadence is part of the control loop.** A frequency command is a setpoint that stays in force until replaced. Applying it late does not lose it, and still produces the wrong integral. Measure the loop's actuation delay, not only its measurement rate.

**Follow the dependency, not the feature.** The affected guest never used PTP. It depended on host time, the host's time depended on clock machinery, and the machinery had been refactored for PTP. The route to the fix ran through what the symptom depended on, not through what the change was for.

**Defined behavior is not correct behavior.** The default sanitizer checks have nothing to say about it. The compiler warns, at a flag most builds do not enable. So state the type invariant and test it.

---

*The hardware-free companion (the pending-slew model with the broken and fixed helpers, and the exact-fraction cadence model) lives at [demo/clock-signed-slew](https://github.com/dshah133/howtf/tree/main/demo/clock-signed-slew). Mechanism sources are pinned inline: the ESXi 7.0 Update 3o release notes and KB 312016, Linux v6.12 (`ptp_vmw.c`, `ntp.c`, `timekeeping.c`, `timex.h`), FreeBSD 13.2 (`kern_ntptime.c`), the Photon OS `ptp_vmw` adjustment patch, LinuxPTP v4.2 and its man pages, VMware and Broadcom documentation, and the C11 draft.*
