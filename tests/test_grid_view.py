"""Unit tests for GridView (second physical grid sensor, dynamic peak limit)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from custom_components.omnibattery.control.grid_view import (
    PHYSICAL_FROZEN_S,
    PHYSICAL_MAX_AGE_S,
    GridView,
)

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
PHYS = "sensor.real_grid"
LIMIT = "sensor.peak_limit"


class _States:
    def __init__(self):
        self._d = {}

    def set(self, entity_id, state, unit=None, age_s=0.0, now=NOW):
        attrs = {"unit_of_measurement": unit} if unit else {}
        ts = now - timedelta(seconds=age_s)
        self._d[entity_id] = SimpleNamespace(
            state=str(state), attributes=attrs, last_reported=ts, last_updated=ts
        )

    def get(self, entity_id):
        return self._d.get(entity_id)


def _view(*, physical=PHYS, inverted=False, limit=None, margin=0.0):
    gv = GridView()
    gv.configure(
        physical_entity=physical,
        physical_inverted=inverted,
        limit_entity=limit,
        limit_margin_pct=margin,
    )
    return gv


def test_v1_no_second_sensor():
    gv = _view(physical=None)
    st = _States()
    st.set(PHYS, 1500)
    assert gv.has_physical is False
    assert gv.read_physical(st, NOW) is None
    assert gv.status()["physical_source"] == "none"
    assert gv.status()["unusable_since"] is None


def test_v2_value_and_inversion():
    st = _States()
    st.set(PHYS, 1500)
    assert _view().has_physical is True
    assert _view().read_physical(st, NOW) == 1500
    assert _view(inverted=True).read_physical(st, NOW) == -1500


def test_v3_unusable_states():
    gv = _view()
    st = _States()
    assert gv.read_physical(st, NOW) is None  # missing entity
    for bad in ("unavailable", "unknown", "abc"):
        st.set(PHYS, bad)
        assert gv.read_physical(st, NOW) is None
    assert gv.status()["physical_source"] == "fallback"


def test_v4_stale_report():
    gv = _view()
    st = _States()
    st.set(PHYS, 100, age_s=PHYSICAL_MAX_AGE_S + 1)
    assert gv.read_physical(st, NOW) is None
    st.set(PHYS, 100, age_s=PHYSICAL_MAX_AGE_S - 1)
    assert gv.read_physical(st, NOW) == 100


def test_v5_kw_unit():
    st = _States()
    st.set(PHYS, 1.5, unit="kW")
    assert _view().read_physical(st, NOW) == 1500
    assert _view(inverted=True).read_physical(st, NOW) == -1500


def test_v6_is_new():
    gv = _view()
    st = _States()
    st.set(PHYS, 100)
    gv.read_physical(st, NOW)
    assert gv.physical_is_new() is True  # first value
    gv.read_physical(st, NOW)
    assert gv.physical_is_new() is False
    st.set(PHYS, 250)
    gv.read_physical(st, NOW)
    assert gv.physical_is_new() is True


def test_peek_physical_has_no_side_effects():
    gv = _view(inverted=True)
    st = _States()
    st.set(PHYS, 100)
    assert gv.peek_physical(st, NOW) == -100
    assert gv.status()["physical_w"] is None  # nothing stored
    assert gv.physical_is_new() is False
    gv.read_physical(st, NOW)
    assert gv.physical_is_new() is True
    st.set(PHYS, 250)
    assert gv.peek_physical(st, NOW) == -250
    assert gv.physical_is_new() is True  # peek did not consume/advance the state
    assert gv.status()["physical_w"] == -100
    # unusable: peek must not start the unusable timer
    st.set(PHYS, "unavailable")
    assert gv.peek_physical(st, NOW) is None
    assert gv.status()["unusable_since"] is None
    assert _view(physical=None).peek_physical(st, NOW) is None


def test_unusable_since_and_status():
    gv = _view()
    st = _States()
    gv.read_physical(st, NOW)
    t1 = NOW + timedelta(seconds=30)
    gv.read_physical(st, t1)
    assert gv.status()["unusable_since"] == NOW  # keeps first failure
    st.set(PHYS, 700, now=t1)
    assert gv.read_physical(st, t1) == 700
    s = gv.status()
    assert s["unusable_since"] is None
    assert s["physical_w"] == 700
    assert s["physical_source"] == "physical"
    assert s["physical_entity"] == PHYS


def test_v7_margin():
    gv = _view(limit=LIMIT, margin=10)
    st = _States()
    st.set(LIMIT, 12000)
    assert gv.peak_limit(st, 13000, NOW) == 10800
    s = gv.status()
    assert s["peak_limit_w"] == 10800
    assert s["peak_limit_source"] == "entity"


def test_v8_out_of_range_is_unusable(caplog):
    # Review 4: out-of-range values are ignored (no clamping), warned once.
    gv = _view(limit=LIMIT)
    st = _States()
    st.set(LIMIT, 100)  # e.g. kW number without unit
    assert gv.peak_limit(st, 13000, NOW) == 13000
    assert gv.status()["peak_limit_source"] == "static"
    st.set(LIMIT, 50000)
    assert gv.peak_limit(st, 13000, NOW) == 13000
    warnings = [r for r in caplog.records if "outside" in r.getMessage()]
    assert len(warnings) == 1  # logged once, not every cycle
    # with a valid history the held value applies
    st.set(LIMIT, 9000)
    assert gv.peak_limit(st, 13000, NOW) == 9000
    st.set(LIMIT, 0)  # transient 0 at a tariff change
    assert gv.peak_limit(st, 13000, NOW + timedelta(minutes=1)) == 9000
    assert gv.status()["peak_limit_source"] == "held"
    assert len([r for r in caplog.records if "outside" in r.getMessage()]) == 2
    # range bounds themselves are valid
    st.set(LIMIT, 500)
    assert gv.peak_limit(st, 13000, NOW) == 500
    st.set(LIMIT, 20000)
    assert gv.peak_limit(st, 13000, NOW) == 20000


def test_frozen_latch_only_after_frozen_time():
    gv = _view()
    st = _States()
    st.set(PHYS, 5000)
    assert gv.read_physical(st, NOW) == 5000
    t1 = NOW + timedelta(seconds=PHYSICAL_FROZEN_S - 1)
    st.set(PHYS, 5000, now=t1)  # same value, fresh timestamp
    assert gv.latch_frozen(t1) is False
    assert gv.read_physical(st, t1) == 5000
    t2 = NOW + timedelta(seconds=PHYSICAL_FROZEN_S + 1)
    st.set(PHYS, 5000, now=t2)
    assert gv.latch_frozen(t2) is True
    assert gv.read_physical(st, t2) is None
    assert gv.status()["physical_source"] == "fallback"
    assert gv.status()["unusable_since"] == t2
    assert gv.peek_physical(st, t2) is None
    # latch is sticky while the value stays the same, even without new latch call
    assert gv.read_physical(st, t2 + timedelta(seconds=5)) is None
    # a changed value clears it
    st.set(PHYS, 5100, now=t2)
    assert gv.read_physical(st, t2 + timedelta(seconds=6)) == 5100
    assert gv.physical_is_new() is True
    assert gv.status()["physical_source"] == "physical"
    assert gv.status()["unusable_since"] is None


def test_frozen_latch_changing_value_never_latches():
    gv = _view()
    st = _States()
    for i in range(5):
        t = NOW + timedelta(seconds=30 * i)
        st.set(PHYS, 5000 + i, now=t)
        assert gv.latch_frozen(t) is False
        assert gv.read_physical(st, t) == 5000 + i


def test_v9_held_value():
    gv = _view(limit=LIMIT)
    st = _States()
    st.set(LIMIT, 9000)
    assert gv.peak_limit(st, 13000, NOW) == 9000
    st.set(LIMIT, "unavailable")
    later = NOW + timedelta(minutes=5)
    assert gv.peak_limit(st, 13000, later) == 9000
    assert gv.status()["peak_limit_source"] == "held"


def test_v10_held_expires():
    gv = _view(limit=LIMIT)
    st = _States()
    st.set(LIMIT, 9000)
    gv.peak_limit(st, 13000, NOW)
    st.set(LIMIT, "unknown")
    assert gv.peak_limit(st, 13000, NOW + timedelta(minutes=16)) == 13000
    assert gv.status()["peak_limit_source"] == "static"


def test_unusable_limit_without_history_is_static():
    gv = _view(limit=LIMIT)
    assert gv.peak_limit(_States(), 13000, NOW) == 13000
    assert gv.status()["peak_limit_source"] == "static"


def test_v11_no_entity_static_without_margin():
    # Margin applies only to the entity value, never to the static limit.
    gv = _view(limit=None, margin=10)
    assert gv.peak_limit(_States(), 13000, NOW) == 13000
    assert gv.status()["peak_limit_source"] == "static"
    assert gv.status()["peak_limit_w"] == 13000
