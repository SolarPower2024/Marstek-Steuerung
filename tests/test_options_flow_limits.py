"""Options flow, battery limits: the form opens with the live values (upstream 72a1c742).

The power sliders store ``user_max_*_power``, which wins at setup, so the form
must open with that (or with the loaded battery's limit) and must write a change
back to it. Walks the steps like ``test_options_flow_mac_tracking``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.omnibattery import config_flow as cf
from custom_components.omnibattery.config_flow import OptionsFlowHandler
from custom_components.omnibattery.const import DOMAIN

STORED = {
    "brand": "marstek",
    "name": "Venus 1",
    "host": "192.0.2.10",
    "port": 502,
    "serial_port": "",
    "rs485_gateway": False,
    "slave_id": 1,
    "battery_version": "v3",
    "max_charge_power": 2500,
    "max_discharge_power": 2500,
    "max_soc": 100,
    "min_soc": 12,
    "charge_hysteresis_percent": 2,
    "backup_offgrid_threshold": 50,
}


@pytest.fixture(autouse=True)
def empty_dhcp_cache(monkeypatch):
    monkeypatch.setattr(cf, "detect_mac", lambda discovered, host: None)


def _options_flow(battery, coordinators=None) -> OptionsFlowHandler:
    entry = SimpleNamespace(entry_id="test-entry", data={"batteries": [battery]}, options={})
    flow = OptionsFlowHandler(entry)
    data = {DOMAIN: {entry.entry_id: {"coordinators": coordinators}}} if coordinators else {}
    flow.hass = SimpleNamespace(
        data=data,
        config_entries=SimpleNamespace(
            async_get_known_entry=lambda entry_id: entry if entry_id == entry.entry_id else None
        ),
    )
    flow.handler = entry.entry_id
    flow._test_connection = AsyncMock(return_value=True)
    return flow


def _defaults(form) -> dict:
    return {
        marker.schema: marker.default()
        for marker in form["data_schema"].schema
        if callable(marker.default)
    }


async def _limits_form(flow):
    form = await flow.async_step_battery_count()
    form = await flow.async_step_battery_count(_defaults(form))
    return await flow.async_step_battery_connection(_defaults(form))


async def _save(flow, user_input):
    with patch.object(
        OptionsFlowHandler, "_save_and_finish", AsyncMock(return_value={"type": "create_entry"})
    ):
        await flow.async_step_battery_limits(user_input)
    return flow.config_data["batteries"][0]


async def test_form_opens_with_the_slider_limit_and_saves_it_back_untouched():
    stored = {**STORED, "user_max_charge_power": 2000, "user_max_discharge_power": 900}
    flow = _options_flow(dict(stored))
    form = await _limits_form(flow)
    defaults = _defaults(form)
    assert (defaults["max_charge_power"], defaults["max_discharge_power"]) == (2000, 900)

    saved = await _save(flow, defaults)
    assert saved["user_max_charge_power"] == 2000
    assert saved["user_max_discharge_power"] == 900


async def test_a_change_in_the_form_reaches_the_slider_limit():
    stored = {**STORED, "user_max_charge_power": 2000, "user_max_discharge_power": 900}
    flow = _options_flow(dict(stored))
    form = await _limits_form(flow)

    saved = await _save(
        flow, {**_defaults(form), "max_charge_power": 1200, "max_discharge_power": 1300}
    )

    assert saved["max_charge_power"] == 1200
    assert saved["user_max_charge_power"] == 1200
    assert saved["user_max_discharge_power"] == 1300


async def test_without_a_slider_value_no_user_key_is_invented():
    flow = _options_flow(dict(STORED))
    form = await _limits_form(flow)
    saved = await _save(flow, _defaults(form))
    assert "user_max_charge_power" not in saved
    assert "user_max_discharge_power" not in saved


async def test_form_shows_what_the_loaded_battery_works_with():
    coordinator = SimpleNamespace(
        configured_max_charge_power=1800, configured_max_discharge_power=60
    )
    flow = _options_flow(dict(STORED), coordinators=[coordinator])
    form = await _limits_form(flow)
    defaults = _defaults(form)
    assert (defaults["max_charge_power"], defaults["max_discharge_power"]) == (1800, 60)

    # A limit under the form's usual 100 W passes through it unchanged.
    saved = await _save(flow, defaults)
    assert saved["max_discharge_power"] == 60
