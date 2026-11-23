# Configure a large load or EV charger

Add a device here when its demand needs different battery treatment from the rest of the home. This page covers the sensors and setup fields; [Load exclusion](../features/load-exclusion.md) explains which behavior to choose and what it does.

## Do I need it?

**Use it if** you want to add a large load, such as an electric vehicle (EV) charger, wallbox, heat pump, or immersion heater, that the battery should ignore fully or partly.

**You do not need it if** the battery should treat the device exactly like ordinary household demand.

## Before you start

- For a metered device, prepare a numeric **Device power sensor** in watts.
- For dynamic power control (DPC), also prepare a **Device active / EV charging sensor** that reports when the device requests power.
- For an EV charger without power telemetry, prepare that activity sensor instead of a watt sensor.
- Decide whether your main home/grid sensor already includes this device's consumption.

## How to enable it

1. Open **Settings → Devices & services → Omnibattery → Configure → Excluded devices**, enable **Configure special devices**, and add a device.
2. Select **Device power sensor**. For a state-only charger, leave it empty, select **Device active / EV charging sensor**, and enable **EV charger without power telemetry**.
3. Set **Consumption is included in home consumption sensor** using the test below.
4. Select the behavior you need: **Allow to use solar surplus (do not charge battery)**, **Device has dynamic power control**, or **Cover home while device is active**. Use the [behavior guide](../features/load-exclusion.md) before combining them, then save the device.

```text
Main sensor measures the whole home, including this device
→ Enable "Consumption is included in home consumption sensor"

Main sensor measures only the domestic circuit and cannot see this device
→ Leave it disabled
```

![Configure an excluded device](../assets/screenshots/configuration/excluded-device-form.png){ width="650" style="display: block; margin: 0 auto;"}

## What you will see

Each saved device exposes live controls on the Omnibattery system device:

| Control | Availability |
|---|---|
| **Device – Enabled** | Every configured device |
| **Device – Solar Surplus** | Every configured device; setup determines its initial state |
| **Device – Dynamic Power Control** | Metered devices; setup determines its initial state |
| **Device – Cover Home** | Every configured device; setup determines its initial state |
| **Device – Exclusion %** | Metered devices |

These entities let you pause or change the saved behavior without reopening setup. See [Load exclusion](../features/load-exclusion.md#what-you-will-see) for the effect of each control.

## If it does not work

| Symptom | Likely cause | What to check |
|---|---|---|
| The form requires a power sensor | The device is configured as a metered load | Select a numeric watt sensor, or enable **EV charger without power telemetry** and provide an activity sensor |
| Dynamic Power Control cannot be saved | Its required activity sensor is missing | Select **Device active / EV charging sensor** |
| The battery compensates the wrong amount | The included-in-consumption choice does not match the main meter | Check whether switching the device changes the main sensor reading |
| **Cover Home** has no useful effect | Solar Surplus or external solar-production data is missing | Enable Solar Surplus and configure the solar production sensor under **Sensors** |
| A runtime control is missing | The device definition does not enable that behavior, or its entity is disabled | Reopen the device configuration and check the Omnibattery device's disabled entities |

??? "Advanced details"
    **Field requirements**

    Omnibattery supports up to 4 configured special devices. A normal excluded device requires a numeric power sensor. A new state-only EV configuration requires an activity sensor. Dynamic Power Control also requires an activity sensor and is meaningful only with Solar Surplus enabled. Cover Home requires Solar Surplus and an external solar-production sensor.

    Existing state-only EV entries that stored their status entity in **Device power sensor** remain supported. Activity detection accepts binary `on` and charging words case-insensitively.
