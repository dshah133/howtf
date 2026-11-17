# clock-signed-slew

Two hardware-free numeric models for the post *howtf did asking the clock to
slow down make it never stop?*

Neither program touches an operating-system clock. Both run on any machine
with a C11 compiler and Python 3.

`signed_clock.c` models the ledger of a pending clock slew, in microseconds.
Once a second it picks a step (5000 us while more than one second is pending,
500 us while more than 500 us is pending, otherwise whatever is left), hands
that step to the clock, and subtracts it from the pending amount. That rule
has the same shape as FreeBSD's `ntp_update_second()` in
`sys/kern/kern_ntptime.c`. It is a model, not ESXi source. One ledger update
copies the signed step through a `uint32_t` temporary, which is the bug the
post describes. The other copies it through an `int32_t`.

The program checks that the broken update, started from any request between
-500 and -1 us, reaches after 72 steps exactly the `adjtime()` value VMware
published with the fix: -309237290312, which is -72 * 2^32 + 71 * 5000. The
sample is in the ESXi 7.0 Update 3o release notes (PR 3246132) and Broadcom
KB 312016. It also reproduces the published tick adjustment to within 1 ns/s,
checks conservation and a finish-time oracle on the fixed path, checks that a
step spread over sub-second callbacks sums to the same step, and evaluates
the invariant that would have caught the corruption: a step moves the pending
amount toward zero and never past it.

`update_cadence.py` integrates a held frequency setpoint with exact fractions.
The requested rate switches from +1 ppm to −1 ppm at 235 ms. Immediate
application is compared with 1 s, 100 ms, and 10 ms callbacks. It is an
open-loop delay example with no servo, so it shows the cost of stale steering
and nothing about oscillation or achieved accuracy.

## Run

```sh
cc -std=c11 -O2 -Wall -Wextra -Wconversion -Wsign-conversion signed_clock.c -o signed_clock
./signed_clock
python3 update_cadence.py
```

Or run everything, including a build with `-fsanitize=undefined`, and
refresh the captured files:

```sh
python3 validate.py
```

The compiler warns at the intentional `uint32_t` line. `-fsanitize=undefined`
does not, because the conversion is defined by C. Clang has a separate opt-in
check for this case, `-fsanitize=implicit-integer-sign-change`.

## Captured output

`expected-output.txt` is the output of both programs. `compiler-warning.txt`
is the `-Wsign-conversion` diagnostic at the intentional bug.
`validation.json` records the compiler, the checks, and the scope.

```
second   broken_step_us   broken_pending_us   fixed_step_us   fixed_pending_us
     1             -300         -4294967296            -300                  0
     2            -5000         -8589929592               0                  0
    72            -5000       -309237290312               0                  0

  adjtime() (usec)            : -309237290312
  model, 72 s, broken helper  : -309237290312  (all 500 requests from -500 to -1 us)

immediate       : integrated =  -0.530 us; difference =  +0.000 us
1 s callback    : integrated =  +1.000 us; difference =  +1.530 us
100 ms callback : integrated =  -0.400 us; difference =  +0.130 us
```
