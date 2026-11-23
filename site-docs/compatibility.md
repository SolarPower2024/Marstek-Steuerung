# Is my battery compatible?

Omnibattery can automatically control the batteries below from Home Assistant. Find your exact model, prepare the connection it needs, and check its limit before you install.

| Brand / model | What you need | What control you get | Important limit | Guide |
|---|---|---|---|---|
| **Marstek Venus E v2** | Direct Modbus connection through an RS-485-to-TCP gateway or USB-to-RS-485 adapter. | Automatic control; solar readings only when its register map supplies them; hardware SOC limits. | Up to 2,500 W. Needs an RS-485 connection, not the battery's Ethernet address. | [Marstek Venus](configuration/batteries/marstek.md) |
| **Marstek Venus A** | A reachable Ethernet address. Tell the wizard whether DC solar is connected. | Automatic control; solar readings when the selected map supplies them; software SOC limits. | Up to 1,500 W. | [Marstek Venus](configuration/batteries/marstek.md) |
| **Marstek Venus D** | A reachable Ethernet address. Tell the wizard whether DC solar is connected. | Automatic control; solar readings when the selected map supplies them; software SOC limits. | 2,200 W; 2,500 W from EMS firmware 149. | [Marstek Venus](configuration/batteries/marstek.md) |

## My battery is not listed

