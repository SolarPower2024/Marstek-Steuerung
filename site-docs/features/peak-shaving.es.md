# Reserva potencia de batería para picos de demanda

Protege tu instalación con protección de capacidad (peak shaving): por debajo de un umbral de estado de carga (SOC), Omnibattery guarda energía para la demanda que superaría el límite de importación de red elegido. Puede reducir picos de potencia contratada y conservar una reserva para más tarde.

## ¿Lo necesito?

**Úsalo si** tu tarifa penaliza picos de importación altos, tu instalación tiene un techo práctico de importación o el seguimiento normal del hogar vacía la batería antes del periodo en que aparecen cargas grandes.

**No lo necesitas si** quieres que la batería cubra la demanda ordinaria del hogar hasta su SOC mínimo normal y no tienes un límite de picos independiente que proteger.

La protección de capacidad es una estrategia de reserva opcional. La protección de emergencia de potencia contratada es independiente y puede seguir protegiendo la conexión física a red.

## Antes de empezar

- Confirma que **Consumo de la casa** y la importación de red tienen el signo correcto y siguen las cargas reales.
- Elige el SOC de batería por debajo del cual debe detenerse la descarga ordinaria.
- Elige el nivel de importación de red por encima del cual debe intervenir la batería. Es independiente de la potencia contratada máxima configurada.
- Si las cargas grandes están [excluidas de la cobertura normal de batería](../configuration/excluded-devices.md), decide si sus picos también deben recortarse.

## Cómo activarlo

1. Abre el panel lateral de Omnibattery y selecciona **Control**.
2. Activa **Peak Shaving**.
3. Configura **Umbral de SOC de Peak Shaving**. Por debajo de este SOC medio de flota, la batería conserva capacidad para picos.
4. Configura **Límite de Peak Shaving** con el umbral de importación de red que quieres mantener.
5. Opcional: activa **Peak Shaving para dispositivos excluidos** si las cargas normalmente excluidas también deben respetar ese límite.

| Ajuste | Predeterminado | Rango |
|---|---:|---:|
| **Umbral de SOC de Peak Shaving** | `30%` | `20–100%` |
| **Límite de Peak Shaving** | `2,500 W` | `500–20,000 W` |

![Configurar la protección de capacidad](../assets/screenshots/configuration/advanced-capacity-protection-config.png){ width="650" style="display: block; margin: 0 auto;"}

## Qué verás

Por encima del umbral de SOC, continúa el seguimiento normal del hogar. Por debajo:

- La demanda igual o inferior al límite de pico se queda en la red para que la batería conserve su reserva.
- La demanda superior al límite se cubre solo con la cantidad necesaria para devolver la importación hacia el límite.
- El excedente solar aún puede cargar la batería.

Por ejemplo, con un límite de `3,000 W` y `4,500 W` de demanda del hogar, la batería suministra `1,500 W` y la red `3,000 W`. Con `2,000 W` de demanda, la batería permanece inactiva.

**Peak Shaving activo** y el estado de integración distinguen entre recortar un pico, conservar capacidad, cargar con excedente y permanecer inactivo. Un enfriamiento de relé configurado puede mantener brevemente la potencia mínima de batería después de que el controlador pida inactividad; es protección esperada del relé, no una nueva decisión de carga o descarga.

![Controles de peak shaving](../assets/screenshots/features/peak-shaving-config.png){ width="650" style="display: block; margin: 0 auto;"}

## Si no funciona

| Síntoma | Causa probable | Qué comprobar |
|---|---|---|
| La batería sigue cubriendo demanda ordinaria | El SOC medio de batería está por encima del umbral de conservación | **Umbral de SOC de Peak Shaving** y **Peak Shaving activo** |
| Un pico permanece por encima del límite | La potencia de descarga disponible o alguna regla de seguridad limita la salida | SOC mínimo, disponibilidad de batería, límites de fase, estado de respaldo y límites de potencia |
| No se recorta una carga excluida | La opción independiente de dispositivos excluidos está desactivada | **Peak Shaving para dispositivos excluidos** |
| La batería no pasa a inactividad inmediatamente | El enfriamiento de relé mantiene potencia mínima o la telemetría se está estabilizando | Enfriamiento de relé PD y potencia instantánea de batería |

??? "Detalles avanzados"
    Por debajo del umbral de conservación, Omnibattery reconstruye la carga del hogar a partir de telemetría de CA de red y batería y aplica este objetivo:

    ```text
    battery_discharge = max(0, household_load - peak_limit)
    ```

    El SOC medio excluye baterías no disponibles y controladas manualmente. Siguen aplicándose SOC mínimo, disponibilidad, control manual o por franja, restricciones de respaldo, protección de fase y límites de potencia de batería/sistema.

    **Peak Shaving para dispositivos excluidos** está desactivado de forma predeterminada. Por encima del umbral de conservación, la cobertura ordinaria del hogar no cambia y solo se vuelve a añadir la parte excluida que dejaría la importación física por encima del límite. Con `1,000 W` de demanda normal, `4,000 W` excluidos y un límite de `3,000 W`, la batería suministra `2,000 W`: `1,000 W` para demanda normal y `1,000 W` para recortar la carga excluida. Por debajo del umbral, la protección de capacidad ya aplica el límite a la demanda total.

    La importación física por encima de la potencia contratada máxima puede activar descarga de emergencia, también para cargas excluidas que vea la conexión de red.

    El enfriamiento de relé es opcional y por defecto es `0 seconds`. Cuando se configura, una solicitud de activo a inactivo puede mantener la dirección ya activa a la potencia mínima configurada, o `100 W` si no se establece mínimo, hasta que vence el enfriamiento seleccionado. Un gran desequilibrio evita esta retención. Protege el relé de ciclos rápidos de apagado/encendido y no retrasa cambios directos de dirección entre carga y descarga.
