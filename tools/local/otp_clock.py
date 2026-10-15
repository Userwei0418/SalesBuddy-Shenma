"""Bounded waiting for synthetic OTP checks; never bypass product replay rules."""
import time


def wait_for_new_step(previous_step, *, clock=time.time, monotonic=time.monotonic,
                      sleep=time.sleep, timeout=40):
    deadline = monotonic() + timeout
    while int(clock()) // 30 <= previous_step:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RuntimeError("Local verification clock did not enter a new OTP window")
        # Recheck the actual clock after waking: VM/NTP adjustments or an early
        # wakeup must not resend a code from the window that just failed.
        until_next = (previous_step + 1) * 30 - clock()
        sleep(min(remaining, max(0.2, min(until_next + 0.25, 1))))
