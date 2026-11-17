"""Exact open-loop application-delay model, not an ESXi/PTP benchmark.

No feedback servo, wall-clock calls, sleeping, scheduling, or clock changes.
Python 3 standard library only. Times are ns; rate commands are ppb.
"""
from fractions import Fraction

NS_PER_SECOND = 1_000_000_000
END_NS = NS_PER_SECOND
COMMANDS = {0: 1000, 235_000_000: -1000}  # +1 ppm, then -1 ppm


def integrate(callback_interval_ns=None):
    """Integrate the applied rate contribution over one reference second.

    A callback copies the latest requested setpoint. Commands at the same
    timestamp are processed before the callback. The clock advances between
    callbacks at the active rate, independently of the callback cadence.
    """
    callbacks = (
        set(COMMANDS)
        if callback_interval_ns is None
        else set(range(0, END_NS + 1, callback_interval_ns))
    )
    events = sorted(set(COMMANDS) | callbacks | {END_NS})
    pending_ppb = active_ppb = previous_ns = 0
    correction_ns = Fraction(0)
    for now_ns in events:
        correction_ns += Fraction(
            active_ppb * (now_ns - previous_ns), NS_PER_SECOND
        )
        if now_ns in COMMANDS:
            pending_ppb = COMMANDS[now_ns]
        if now_ns in callbacks:
            active_ppb = pending_ppb
        previous_ns = now_ns
    return correction_ns


def main():
    results = [(name, integrate(period)) for name, period in [
        ("immediate", None), ("1 s callback", NS_PER_SECOND),
        ("100 ms callback", 100_000_000), ("10 ms callback", 10_000_000),
    ]]
    # Independent geometric areas under the two held rate segments.
    expected_ns = [-530, 1000, -400, -520]
    ideal = results[0][1]
    for (name, value), expected in zip(results, expected_ns):
        assert value == expected
        print(f"{name:16s}: integrated = {float(value / 1000):+7.3f} us; "
              f"difference = {float((value - ideal) / 1000):+7.3f} us")
    # Event-aligned callbacks apply every change without delay.
    assert integrate(5_000_000) == ideal
    print("PASS: four analytic areas and event-aligned control case.")


if __name__ == "__main__":
    main()
