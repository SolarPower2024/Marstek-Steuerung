# ¿Es compatible mi batería?

Omnibattery puede controlar automáticamente desde Home Assistant las baterías siguientes. Busca tu modelo exacto, prepara la conexión que necesita y comprueba su límite antes de instalar.

| Marca / modelo | Lo que necesitas | Qué control obtienes | Límite importante | Guía |
|---|---|---|---|---|
| **Marstek Venus E v2** | Conexión Modbus directa mediante una pasarela RS-485 a TCP o un adaptador USB a RS-485. | Control automático; lecturas solares solo cuando las proporciona su mapa de registros; límites de SOC del hardware. | Hasta 2.500 W. Necesita una conexión RS-485, no la dirección Ethernet de la batería. | [Marstek Venus](configuration/batteries/marstek.md) |
| **Marstek Venus A** | Una dirección Ethernet accesible. Indica al asistente si hay solar CC conectada. | Control automático; lecturas solares cuando las proporciona el mapa seleccionado; límites de SOC por software. | Hasta 1.500 W. | [Marstek Venus](configuration/batteries/marstek.md) |
| **Marstek Venus D** | Una dirección Ethernet accesible. Indica al asistente si hay solar CC conectada. | Control automático; lecturas solares cuando las proporciona el mapa seleccionado; límites de SOC por software. | 2.200 W; 2.500 W con firmware EMS 149. | [Marstek Venus](configuration/batteries/marstek.md) |

## Mi batería no aparece

