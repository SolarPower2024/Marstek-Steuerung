"""Anti-chatter guard for the battery relay (fork addition, pure logic).

No Home Assistant imports. Time is passed in as ``now`` (float seconds,
monotonic). Sign convention: power + = charge, - = discharge; error + = grid
draw above target (discharge demand), error - = surplus (charge demand).

The guard only ever passes a requested power through unchanged or replaces it
with 0; it never raises or creates power, so it cannot defeat a protection.

Specification gaps and decisions:
* ``budget_per_hour <= 0`` disables the throttle; ``bypass_error_w <= 0``
  disables the error bypass (otherwise every error would bypass).
* Before the first ``observe`` (or without a recorded shut-off) no lock applies.
  The very first observation only initialises the state, it is no switch.
* A direct flip (charge <-> discharge without idle) counts as one switch but is
  no shut-off: it starts no ``min_off_s`` and, because the new direction is then
  the last active one, no direction lock either. After the direct-flip
  ``direction_lock_s`` check, the engage threshold of the new direction applies.
* ``reason`` / ``remaining_s`` of a stale pass are not stored: ``stale_recalc``
  leaves all state, including ``status()["state"]``, untouched.
* ``engage_threshold`` has ``remaining_s`` 0 (no time-based lock).
* ``blocked_today`` counts blocking episodes (a block after a non-block
  decision), not every blocked cycle. ``switches_today`` / ``blocked_today``
  are cleared by ``reset_daily()`` (called by the owner at midnight).
* ``last_switch`` is the ``now`` value of the last switch (monotonic seconds),
  or None; the caller may convert it to a wall-clock time for display.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

_WINDOW_S = 3600.0
_THROTTLE_FACTOR = 3.0
_BLOCK_REASONS = ("min_off", "direction_lock", "engage_threshold")


@dataclass
class GuardDecision:
    power: float  # passed through or set to 0
    reason: str  # pass, min_off, direction_lock, engage_threshold,
    # bypass_error, bypass_peak, disabled
    remaining_s: float = 0.0  # remaining time of the active lock, else 0


def _sign(p: float) -> int:
    return (p > 0) - (p < 0)


class RelayGuard:
    def __init__(self) -> None:
        self._enabled = True
        self._min_off_s = 0.0
        self._direction_lock_s = 0.0
        self._engage_margin_w = 0.0
        self._bypass_error_w = 0.0
        self._budget = 0
        self._state: int | None = None  # observed -1 / 0 / 1
        self._last_dir = 0  # last active direction
        self._dir_start: float | None = None  # start of current direction
        self._dir_end: float | None = None  # end of last dir (set on idle)
        self._last_off: float | None = None  # last shut-off to idle
        self._last_switch: float | None = None
        self._switches: deque[float] = deque()
        self._switches_today = 0
        self._blocked_today = 0
        self._in_block = False
        self._reason = "pass"
        self._until: float | None = None

    def configure(self, *, enabled: bool, min_off_s: float,
                  direction_lock_s: float, engage_margin_w: float,
                  bypass_error_w: float, budget_per_hour: int) -> None:
        self._enabled = bool(enabled)
        self._min_off_s = float(min_off_s)
        self._direction_lock_s = float(direction_lock_s)
        self._engage_margin_w = float(engage_margin_w)
        self._bypass_error_w = float(bypass_error_w)
        self._budget = int(budget_per_hour)

    def reset_daily(self) -> None:
        self._switches_today = 0
        self._blocked_today = 0

    def observe(self, commanded_power: float, now: float) -> None:
        """Track state changes of the command actually sent (once per cycle)."""
        s = _sign(commanded_power)
        if self._state is None:
            self._state = s
            if s:
                self._last_dir, self._dir_start = s, now
            return
        if s == self._state:
            return
        self._switches.append(now)
        self._switches_today += 1
        self._last_switch = now
        if s == 0:
            self._last_off = self._dir_end = now
        else:
            self._last_dir, self._dir_start, self._dir_end = s, now, None
        self._state = s

    def _count(self, now: float) -> int:
        while self._switches and self._switches[0] <= now - _WINDOW_S:
            self._switches.popleft()
        return len(self._switches)

    def _factor(self, now: float) -> float:
        throttled = self._budget > 0 and self._count(now) >= self._budget
        return _THROTTLE_FACTOR if throttled else 1.0

    def _result(self, power: float, reason: str, remaining: float = 0.0,
                until: float | None = None) -> GuardDecision:
        blocked = reason in _BLOCK_REASONS
        if blocked and not self._in_block:
            self._blocked_today += 1
        self._in_block = blocked
        self._reason = reason
        self._until = until if blocked else None
        return GuardDecision(power, reason, remaining if blocked else 0.0)

    def filter(self, requested: float, error: float, *, previous_power: float,
               min_charge_w: float, min_discharge_w: float, peak_bypass: bool,
               stale_recalc: bool, now: float) -> GuardDecision:
        if not self._enabled:
            return self._result(requested, "disabled")
        if stale_recalc:
            return GuardDecision(requested, "pass", 0.0)
        if peak_bypass:
            return self._result(requested, "bypass_peak")
        if self._bypass_error_w > 0 and abs(error) >= self._bypass_error_w:
            return self._result(requested, "bypass_error")
        req, prev = _sign(requested), _sign(previous_power)
        if req == 0 or req == prev:
            return self._result(requested, "pass")

        factor = self._factor(now)
        if prev == 0:  # engage from idle
            if self._last_off is not None:
                end = self._last_off + self._min_off_s * factor
                if now < end:
                    return self._result(0.0, "min_off", end - now, end)
            if (self._last_dir not in (0, req) and self._dir_end is not None):
                end = self._dir_end + self._direction_lock_s * factor
                if now < end:
                    return self._result(0.0, "direction_lock", end - now, end)
        elif self._dir_start is not None:  # direct direction flip
            end = self._dir_start + self._direction_lock_s * factor
            if now < end:
                return self._result(0.0, "direction_lock", end - now, end)

        min_w = min_charge_w if req > 0 else min_discharge_w
        if min_w > 0:
            demand = -error if req > 0 else error
            if demand < min_w + self._engage_margin_w:
                return self._result(0.0, "engage_threshold")
        return self._result(requested, "pass")

    def status(self, now: float) -> dict:
        remaining = 0.0
        if self._reason in _BLOCK_REASONS and self._until is not None:
            remaining = max(0.0, self._until - now)
        count = self._count(now)
        return {
            "state": self._reason,
            "remaining_s": remaining,
            "switches_last_hour": count,
            "switches_today": self._switches_today,
            "blocked_today": self._blocked_today,
            "throttled": self._budget > 0 and count >= self._budget,
            "last_switch": self._last_switch,
        }
