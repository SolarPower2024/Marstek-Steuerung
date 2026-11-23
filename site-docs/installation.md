# Install Omnibattery

Install the integration, connect a supported battery and choose the Home Assistant sensor that measures your home's grid exchange. You can finish a basic setup first and add optional sensors and schedules later.

Already using **Marstek Venus Energy Manager**? Stop here and follow [Upgrading from Marstek VEM](upgrading-from-marstek-vem.md) to preserve your configuration and history.

Check [whether your exact battery is compatible](compatibility.md) before installing.

## Before you start

You need:

- Home Assistant **2024.4.1** or later
- a [supported battery](configuration/batteries/index.md) reachable from Home Assistant
- a Home Assistant power sensor that measures total grid import and export in watts or kilowatts

The battery or its bridge must be reachable over the relevant local connection. A grid sensor is required because Omnibattery uses it to decide how much the batteries should charge or discharge.

| If you have | Prepare this before setup | Connection |
|---|---|---|
| **Marstek Venus E v2/v3, Venus A or Venus D** | Enable or add the connection described in the [Marstek guide](configuration/batteries/marstek.md). | Modbus TCP, Modbus RTU over USB–RS-485 for Venus E v2 |

!!! warning "Use a responsive grid sensor"
    An update every **1–2 seconds** is recommended. Sensors that publish every **10 seconds or more** are accepted, but Omnibattery raises a Repairs warning because delayed readings reduce control quality. The latest reading remains valid for up to **65 seconds**.

    See [Main sensor](configuration/main-sensor.md) for sign conventions, units and meter-specific guidance.

### Optional information

You can leave these fields empty or disabled during the first setup and add them later through **Settings → Devices & services → Omnibattery → Configure**:

- **Solar production sensor** when an external inverter measures panels that do not feed the battery's own solar inputs
- **Off-grid power sensor** when a separate meter measures the backed-up circuit
- **Three-phase current protection** when phase-current sensors are available; see [Three-phase current protection](configuration/three-phase.md)

Set **Maximum contracted power** to the actual import limit for your home. Omnibattery uses it as a charging safety ceiling.

??? "Connection details by battery type"
    **Marstek:** Venus E v2 needs an RS-485 connection, such as a USB adapter or an RS-485-to-TCP converter. Venus E v3, Venus A and Venus D can use their network connection.

## Install with HACS

Home Assistant Community Store (HACS) is the recommended installation method.

1. Use the button below to add the repository to HACS.

    [![Add Omnibattery to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ffunes&repository=Omnibattery&category=integration)

2. In HACS, search for **Omnibattery** and select **Download**.
3. Restart Home Assistant when HACS asks you to.

## Install manually

1. Download the zip file from the [latest Omnibattery release](https://github.com/ffunes/Omnibattery/releases).
2. Extract the `omnibattery` folder into your Home Assistant `custom_components` directory.
3. Confirm the resulting path is `custom_components/omnibattery/manifest.json`.
4. Restart Home Assistant.

## Add the integration

1. Open **Settings → Devices & services**.
2. Select **Add integration** and search for **Omnibattery**.
3. Choose your grid sensor and enter your installation's electrical settings.
4. Add each battery, then finish or configure the optional time slots and excluded devices sections.

![Add Omnibattery from Home Assistant's integration dialog](assets/screenshots/installation/add-integration.png){ width="600" style="display: block; margin: 0 auto;" }

The [configuration guide](configuration/index.md) explains each page of the setup wizard.

## Check the result

After the wizard finishes:

- open the Omnibattery sidebar panel and confirm that **Grid**, **Home** and **Battery** show plausible power values
- open **Batteries** and confirm that each battery reports its state of charge (SOC) and power
- open **Control** to enable only the optional features you want

If the battery is unavailable or its values have the wrong sign, use the relevant [battery setup guide](configuration/batteries/index.md) and [Troubleshooting](troubleshooting.md) before enabling automatic control.

## Blueprint installation

Blueprints are optional and are installed separately from the integration. Omnibattery works without them. When the basic integration is operating correctly, see [Blueprints](automations/blueprints.md) for Home Assistant and manual installation steps.
