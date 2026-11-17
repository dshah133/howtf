/*
 * Pending-slew model for the post "howtf did the clock just keep slowing
 * down?" (howtf.io). Illustrative, not ESXi, FreeBSD, or Linux source.
 *
 * All quantities named *_us are microseconds. One step is one second.
 * No operating-system clocks, syscalls, sleeping, or network access.
 */
#include <assert.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#define WRAP32 INT64_C(4294967296)

/* Published by VMware for the fix: ESXi 7.0 Update 3o release notes, PR 3246132,
 * and Broadcom KB 312016, output of vsish -e get /system/ntpclock/clockData. */
#define PUBLISHED_ADJTIME_US INT64_C(-309237290312)
#define PUBLISHED_TICK_ADJ_NS INT64_C(-4987251)
#define PUBLISHED_FREQ_NS INT64_C(12888)
#define PUBLISHED_OFFSET_NS INT64_C(-143603)
#define PUBLISHED_POLL_SHIFT 6
#define SHIFT_PLL 4 /* FreeBSD sys/sys/timex.h */

struct ledger {
    int64_t request_us;   /* what was asked for */
    int64_t remaining_us; /* correction still pending */
    int64_t applied_us;   /* correction handed to the clock so far */
};

/*
 * How much of the pending amount to apply during one second. Same shape as
 * FreeBSD's ntp_update_second(): 5000 us beyond one second pending, 500 us
 * beyond 500 us pending, otherwise whatever is left.
 */
static int32_t step_for(int64_t remaining_us)
{
    if (remaining_us > 1000000) return 5000;
    if (remaining_us < -1000000) return -5000;
    if (remaining_us > 500) return 500;
    if (remaining_us < -500) return -500;
    return (int32_t)remaining_us;
}

static void account_broken(int64_t *remaining_us, int32_t step_us)
{
    uint32_t temporary = step_us; /* deliberate bug and compiler warning */
    *remaining_us -= temporary;
}

static void account_fixed(int64_t *remaining_us, int32_t step_us)
{
    int32_t temporary = step_us; /* the one-line fix */
    *remaining_us -= temporary;
}

static int64_t magnitude(int64_t v)
{
    return v < 0 ? -v : v;
}

/* The invariant a guard would check: a step moves the pending amount toward
 * zero and never past it. */
static bool step_is_sane(int64_t before_us, int64_t after_us)
{
    if (magnitude(after_us) > magnitude(before_us)) return false;
    if (before_us > 0 && after_us < 0) return false;
    if (before_us < 0 && after_us > 0) return false;
    return true;
}

/* One second. Returns whether the ledger update kept the invariant. */
static bool one_second(struct ledger *l, bool broken)
{
    int64_t before_us = l->remaining_us;
    int32_t step_us = step_for(before_us);
    l->applied_us += step_us; /* the clock gets the signed step either way */
    if (broken)
        account_broken(&l->remaining_us, step_us);
    else
        account_fixed(&l->remaining_us, step_us);
    return step_is_sane(before_us, l->remaining_us);
}

static struct ledger run(int64_t request_us, bool broken, int64_t seconds)
{
    struct ledger l = { request_us, request_us, 0 };
    for (int64_t s = 0; s < seconds; ++s)
        (void)one_second(&l, broken);
    return l;
}

/* Independent closed-form oracle: seconds a correct ledger needs to finish. */
static int64_t expected_seconds(int64_t request_us)
{
    int64_t left = magnitude(request_us), seconds = 0;
    if (left > 1000000) {
        int64_t n = (left - 1000000 + 4999) / 5000;
        seconds += n;
        left -= n * 5000;
    }
    if (left > 500) {
        int64_t n = (left - 500 + 499) / 500;
        seconds += n;
        left -= n * 500;
    }
    return left > 0 ? seconds + 1 : seconds;
}

/* Spread one second's step over sub-second callbacks. The total must not
 * depend on how often the callback runs. */
static int64_t spread_over_callbacks(int32_t step_us, int64_t callbacks)
{
    int64_t total = 0;
    for (int64_t i = 0; i < callbacks; ++i)
        total += (int64_t)step_us * (i + 1) / callbacks
               - (int64_t)step_us * i / callbacks;
    return total;
}

int main(void)
{
    const int32_t steps[] = {-5000, -500, -1, 0, 1, 500, 5000};
    const int64_t requests[] = {-2000000, -1000001, -500000, -1000, -501,
                                -500, -300, -1, 0, 1, 300, 1000, 2000000};
    const int64_t callbacks_per_second[] = {1, 7, 10, 100, 1000};
    unsigned sign_cases = 0, trajectory_cases = 0, cadence_cases = 0;

    puts("MODEL: pending-slew ledger, one step per second.");
    puts("Step rule: 5000 us beyond 1 s pending, 500 us beyond 500 us, else the rest.");
    puts("step_us   broken_subtracts   fixed_subtracts");
    for (unsigned i = 0; i < sizeof steps / sizeof steps[0]; ++i) {
        int32_t step = steps[i];
        int64_t bad = 0, good = 0;
        account_broken(&bad, step);
        account_fixed(&good, step);
        assert(-good == step);
        assert(-bad == (step < 0 ? (int64_t)step + WRAP32 : step));
        printf("%7" PRId32 "   %16" PRId64 "   %15" PRId64 "\n", step, -bad, -good);
        ++sign_cases;
        for (unsigned j = 0; j < sizeof callbacks_per_second / sizeof callbacks_per_second[0]; ++j) {
            assert(spread_over_callbacks(step, callbacks_per_second[j]) == step);
            ++cadence_cases;
        }
    }

    for (unsigned i = 0; i < sizeof requests / sizeof requests[0]; ++i) {
        int64_t q = requests[i];
        int64_t need = expected_seconds(q);
        struct ledger good = { q, q, 0 };
        for (int64_t s = 0; s < need + 5; ++s) {
            assert(one_second(&good, false));                  /* invariant holds */
            assert(good.applied_us + good.remaining_us == q);  /* conservation */
            assert((good.remaining_us == 0) == (s + 1 >= need));
        }
        assert(good.applied_us == q);
        ++trajectory_cases;

        struct ledger bad = { q, q, 0 };
        bool sane = one_second(&bad, true);
        if (q >= 0) {
            /* Positive requests survive the unsigned copy unchanged. */
            struct ledger same = run(q, true, need + 5);
            assert(same.remaining_us == 0 && same.applied_us == q);
            assert(sane);
        } else {
            /* Every negative request breaks the invariant on the first step. */
            assert(!sane);
            assert(bad.applied_us + bad.remaining_us == q - WRAP32);
        }
        ++trajectory_cases;
    }

    puts("\nRequest: -300 us. Nothing replaces the pending amount.");
    puts("second   broken_step_us   broken_pending_us   fixed_step_us   fixed_pending_us");
    const int64_t at[] = {1, 2, 3, 72, 3600};
    for (unsigned i = 0; i < sizeof at / sizeof at[0]; ++i) {
        struct ledger bad_before = run(-300, true, at[i] - 1);
        struct ledger good_before = run(-300, false, at[i] - 1);
        struct ledger bad = run(-300, true, at[i]);
        struct ledger good = run(-300, false, at[i]);
        printf("%6" PRId64 "   %14" PRId64 "   %17" PRId64 "   %13" PRId64 "   %16" PRId64 "\n",
               at[i], bad.applied_us - bad_before.applied_us, bad.remaining_us,
               good.applied_us - good_before.applied_us, good.remaining_us);
    }
    struct ledger hour = run(-300, true, 3600);
    printf("clock slowed after 3600 s: broken %.6f s, fixed %.6f s\n",
           (double)-hour.applied_us / 1000000, 0.000300);

    puts("\nPublished sample, ESXi 7.0 U3o release note (PR 3246132):");
    printf("  adjtime() (usec)            : %" PRId64 "\n", PUBLISHED_ADJTIME_US);
    assert(PUBLISHED_ADJTIME_US == -72 * WRAP32 + 71 * 5000);
    printf("  -72 * 2^32 + 71 * 5000      : %" PRId64 "\n", -72 * WRAP32 + 71 * 5000);
    unsigned matches = 0;
    for (int64_t q = -500; q <= -1; ++q) {
        assert(run(q, true, 72).remaining_us == PUBLISHED_ADJTIME_US);
        ++matches;
    }
    assert(run(-501, true, 72).remaining_us != PUBLISHED_ADJTIME_US);
    assert(run(-300, true, 71).remaining_us != PUBLISHED_ADJTIME_US);
    assert(run(-300, true, 73).remaining_us != PUBLISHED_ADJTIME_US);
    assert(run(-300, false, 72).remaining_us == 0);
    printf("  model, 72 s, broken helper  : %" PRId64 "  (all %u requests from -500 to -1 us)\n",
           run(-300, true, 72).remaining_us, matches);

    int64_t phase_ns = PUBLISHED_OFFSET_NS / (INT64_C(1) << (SHIFT_PLL + PUBLISHED_POLL_SHIFT));
    int64_t tick_ns = PUBLISHED_FREQ_NS + phase_ns + (int64_t)step_for(PUBLISHED_ADJTIME_US) * 1000;
    assert(magnitude(tick_ns - PUBLISHED_TICK_ADJ_NS) <= 1);
    printf("  tick adjustment (nsec/sec)  : %" PRId64 " published; %" PRId64 " + %" PRId64 " + %d = %" PRId64 " modeled\n",
           PUBLISHED_TICK_ADJ_NS, PUBLISHED_FREQ_NS, phase_ns,
           step_for(PUBLISHED_ADJTIME_US) * 1000, tick_ns);

    printf("\nPASS: %u signed cases; %u trajectory cases; %u cadence cases; "
           "published value reproduced; conservation; step invariant; closed-form oracle.\n",
           sign_cases, trajectory_cases, cadence_cases);
    return 0;
}
