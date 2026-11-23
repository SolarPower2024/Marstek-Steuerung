"""#898: "now" and "today" are Home Assistant local time, not the process (OS) clock.

Home Assistant never sets the process time zone, so a Docker/Core install
without TZ runs the OS clock on UTC while schedules and stored dates are in
Home Assistant local time.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.util import dt as dt_util

from custom_components.omnibattery.control import weekly_full_charge as weekly_module
from custom_components.omnibattery.control.weekly_full_charge import WeeklyFullChargeManager
from custom_components.omnibattery.infra.clock import local_now

HA_TODAY = date(2026, 11, 19)
OS_TODAY = date(2026, 11, 18)


class _OsDate(date):
    """The date a UTC OS clock still shows in the first hour of the Brussels day."""

    @classmethod
    def today(cls):
        return OS_TODAY


@pytest.fixture
def brussels_just_after_midnight(monkeypatch):
    previous = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(ZoneInfo("Europe/Brussels"))
    # 00:30 on the 19th in Brussels is 23:30 on the 18th on the UTC OS clock.
    instant = datetime(2026, 11, 18, 23, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(dt_util, "utcnow", lambda: instant)
    monkeypatch.setattr(
        dt_util, "now", lambda time_zone=None: instant.astimezone(time_zone or dt_util.DEFAULT_TIME_ZONE)
    )
    monkeypatch.setattr(weekly_module, "date", _OsDate)
    yield
    dt_util.set_default_time_zone(previous)


def test_local_now_follows_the_home_assistant_zone(brussels_just_after_midnight):
    assert local_now() == datetime(2026, 11, 19, 0, 30)
    assert local_now().tzinfo is None


@pytest.mark.asyncio
async def test_weekly_state_is_saved_under_the_home_assistant_date(brussels_just_after_midnight):
    manager = WeeklyFullChargeManager.__new__(WeeklyFullChargeManager)
    manager._controller = SimpleNamespace(
        weekly_full_charge_enabled=True,
        weekly_full_charge_complete=True,
        weekly_full_charge_registers_written=True,
        _weekly_charge_status={"state": "Complete"},
        _weekly_charge_needs_restore=False,
        _weekly_charge_saved_max_soc={},
    )
    manager._store = SimpleNamespace(async_save=AsyncMock())

    await manager.save_state()

    assert manager._store.async_save.await_args.args[0]["date"] == HA_TODAY.isoformat()


@pytest.mark.asyncio
async def test_weekly_state_of_the_home_assistant_day_is_restored(brussels_just_after_midnight):
    # Thursday the 19th is the weekly day; the run was saved a moment ago.
    controller = SimpleNamespace(
        weekly_full_charge_enabled=True,
        weekly_full_charge_day="thu",
        weekly_full_charge_complete=False,
        weekly_full_charge_registers_written=False,
        _weekly_charge_status={"state": "Idle"},
        _weekly_charge_needs_restore=False,
        _weekly_charge_saved_max_soc={},
    )
    manager = WeeklyFullChargeManager.__new__(WeeklyFullChargeManager)
    manager._controller = controller
    manager._store = SimpleNamespace(
        async_load=AsyncMock(
            return_value={"date": HA_TODAY.isoformat(), "complete": True, "registers_written": True}
        )
    )

    await manager.load_state()

    assert controller.weekly_full_charge_complete is True
