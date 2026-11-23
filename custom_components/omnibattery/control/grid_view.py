"""GridView: optional second (physical) grid sensor and dynamic peak limit.

Pure logic, no Home Assistant imports. ``states`` is any object with
``.get(entity_id)`` returning a State-like object (``.state``,
``.attributes``, ``.last_reported`` / ``.last_updated``). ``now`` is a
timezone-aware datetime supplied by the caller.

Sign convention: positive = grid import, negative = export.

Design decision: ``read_physical`` returns the raw (unsmoothed) value. EMA
smoothing was left out because it would make exact-value expectations and
``physical_is_new`` (value changed vs. previous read) ambiguous.

Age limit (``PHYSICAL_MAX_AGE_S``): the age is taken from ``last_reported``
(falls back to ``last_updated`` on HA versions without it). A meter
integration that re-writes an identical value advances ``last_reported`` and
stays usable. Sensors that only write on a CHANGE do not: a template sensor
whose inputs did not change, or a sensor with a ``delta`` /
``throttle`` filter, becomes unusable after 60 s of identical readings. The
caller then falls back to the control sensor; while peak shaving runs, the
controller's peak exit hold (PEAK_EXIT_HOLD_S) keeps the last command for a
few seconds before stopping, so a quiet meter does not cut a peak discharge
instantly. Choose a meter that publishes regularly.

Frozen value (``PHYSICAL_FROZEN_S``): a meter whose integration keeps
re-reporting a cached value looks fresh by timestamp. The controller calls
``latch_frozen`` only while peak shaving runs on the real meter; once the
value has not changed for longer than PHYSICAL_FROZEN_S the reading is
treated as unusable ("fallback") until the value changes again. Outside peak
shaving there is no frozen check (a quiet meter at low load is normal).

Limit entity: a value outside PEAK_LIMIT_MIN_W..PEAK_LIMIT_MAX_W (e.g. a kW
number without unit, or a transient 0) is treated as unusable (held value,
else static limit) and logged once, instead of being clamped.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

_LOGGER = logging.getLogger(__name__)

PHYSICAL_MAX_AGE_S = 60
PHYSICAL_FROZEN_S = 60
PEAK_LIMIT_MIN_W = 500.0
PEAK_LIMIT_MAX_W = 20000.0
PEAK_LIMIT_HOLD = timedelta(minutes=15)


def _read_watts(state) -> float | None:
    """Numeric state in W (kW converted), or None if unusable."""
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    try:
        value = float(state.state)
    except (ValueError, TypeError):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    attributes = getattr(state, "attributes", None) or {}
    if attributes.get("unit_of_measurement", "W") == "kW":
        value *= 1000.0
    return value


class GridView:
    def __init__(self) -> None:
        self._physical_entity: str | None = None
        self._inverted = False
        self._limit_entity: str | None = None
        self._margin_pct = 0.0
        self._last_w: float | None = None
        self._prev_w: float | None = None
        self._unusable_since: datetime | None = None
        self._changed_at: datetime | None = None
        self._frozen = False
        self._source = "none"
        self._held_w: float | None = None
        self._held_at: datetime | None = None
        self._limit_w: float | None = None
        self._limit_source = "static"
        self._limit_warned = False

    def configure(self, *, physical_entity, physical_inverted,
                  limit_entity, limit_margin_pct) -> None:
        self._physical_entity = physical_entity or None
        self._inverted = bool(physical_inverted)
        self._limit_entity = limit_entity or None
        self._margin_pct = float(limit_margin_pct or 0.0)
        if not self._physical_entity:
            self._last_w = self._prev_w = self._unusable_since = None
            self._changed_at = None
            self._frozen = False
            self._source = "none"

    @property
    def has_physical(self) -> bool:
        return self._physical_entity is not None

    def _sample(self, states, now) -> float | None:
        """Usable, sign-corrected value in W, or None. No state is modified."""
        state = states.get(self._physical_entity)
        value = _read_watts(state)
        if value is not None:
            ts = getattr(state, "last_reported", None) or getattr(
                state, "last_updated", None)
            if ts is not None and (now - ts).total_seconds() > PHYSICAL_MAX_AGE_S:
                value = None
        if value is not None and self._inverted:
            value = -value
        return value

    def peek_physical(self, states, now) -> float | None:
        """Like ``read_physical`` but without side effects.

        For statistics/display: leaves the new-sample detection
        (``physical_is_new``), the source and ``unusable_since`` untouched.
        A latched frozen value stays unusable here as well.
        """
        if not self._physical_entity:
            return None
        value = self._sample(states, now)
        if value is not None and self._frozen and value == self._last_w:
            return None
        return value

    def latch_frozen(self, now) -> bool:
        """Mark the value unusable if unchanged for > PHYSICAL_FROZEN_S.

        Called by the controller only while peak shaving runs on the real
        meter. The latch clears on the next value change (``read_physical``).
        """
        if (self._physical_entity and self._changed_at is not None
                and (now - self._changed_at).total_seconds() > PHYSICAL_FROZEN_S):
            self._frozen = True
        return self._frozen

    def _mark_unusable(self, now) -> None:
        if self._unusable_since is None:
            self._unusable_since = now
        self._source = "fallback"

    def read_physical(self, states, now) -> float | None:
        """Real grid power in W (+import / -export), or None if unusable."""
        if not self._physical_entity:
            self._source = "none"
            return None
        value = self._sample(states, now)
        if value is None:
            self._mark_unusable(now)
            return None
        if value != self._last_w or self._changed_at is None:
            self._changed_at = now
            self._frozen = False
        if self._frozen:
            # Same cached value as before: no new sample, still unusable.
            self._prev_w = self._last_w
            self._mark_unusable(now)
            return None
        self._unusable_since = None
        self._prev_w = self._last_w
        self._last_w = value
        self._source = "physical"
        return value

    def physical_is_new(self) -> bool:
        """True if the last value read differs from the one read before."""
        return self._last_w is not None and self._last_w != self._prev_w

    def peak_limit(self, states, static_limit_w, now) -> float:
        """Effective peak limit in W.

        The margin applies only to the entity value, not to the static limit.
        An entity value outside PEAK_LIMIT_MIN_W..PEAK_LIMIT_MAX_W counts as
        unusable (held value, else static), with a single log warning.
        """
        if self._limit_entity:
            value = _read_watts(states.get(self._limit_entity))
            if value is not None and not PEAK_LIMIT_MIN_W <= value <= PEAK_LIMIT_MAX_W:
                if not self._limit_warned:
                    self._limit_warned = True
                    _LOGGER.warning(
                        "Peak limit entity %s reports %.0f W, outside %.0f-%.0f W; "
                        "ignoring it (held or static limit applies)",
                        self._limit_entity, value, PEAK_LIMIT_MIN_W, PEAK_LIMIT_MAX_W,
                    )
                value = None
            if value is not None:
                self._limit_warned = False
                value *= 1.0 - self._margin_pct / 100.0
                self._held_w, self._held_at = value, now
                return self._set_limit(value, "entity")
            if (self._held_w is not None and self._held_at is not None
                    and now - self._held_at <= PEAK_LIMIT_HOLD):
                return self._set_limit(self._held_w, "held")
        return self._set_limit(static_limit_w, "static")

    def _set_limit(self, value, source) -> float:
        self._limit_w, self._limit_source = value, source
        return value

    def status(self) -> dict:
        return {
            "physical_entity": self._physical_entity,
            "physical_w": self._last_w,
            "physical_source": self._source,
            "unusable_since": self._unusable_since,
            "peak_limit_w": self._limit_w,
            "peak_limit_source": self._limit_source,
        }
