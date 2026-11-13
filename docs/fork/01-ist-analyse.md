# 1. Ist-Analyse (v1.6.0)

Alle Angaben: `custom_components/omnibattery/__init__.py`, Klasse `ChargeDischargeController`, sofern nicht anders genannt.

## 1.1 Reihenfolge der Regelstufen in `async_update_charge_discharge`

| # | Stufe | Stelle | Anmerkung |
|---|---|---|---|
| 1 | Sperren für aktive Batterien (Entlade-/Ladeblock) | ~9960–9968 | Stoppt direkt, an allen Haltezeiten vorbei |
| 2 | Netzsensor lesen, `_apply_meter_transform` | 9974 | einziger Netzsensor |
| 3 | Frische-Prüfung: „kein neuer Regelwert → letzten Befehl halten“ | 10015–10052 | früher Rücksprung, siehe 1.4 |
| 4 | EMA-Filter `_filter_grid_sample` → `sensor_filtered` | 10070 | |
| 5 | Korrekturen: manuelle Batterie, ausgeschlossene Geräte → `sensor_actual` | 10082–10106 | |
| 6 | Stündliche Netto-Bilanz (Setpoint-Offset) | 10111 | |
| 7 | `_apply_capacity_protection` (Peak Shaving) | 10119, Def. 5133 | setzt Setpoint-Override, kann `sensor_actual` verändern |
| 8 | `_apply_icp_excluded_protection` | 10120, Def. 5316 | |
| 9 | Force-Idle bei „conserving“ | 10122–10136 | stoppt direkt, Rücksprung |
| 10 | Totband-Prüfung, Rücksprung | 10143–10192 | letzter Befehl bleibt stehen |
| 11 | First-Execution-Pfad | ~10204–10322 | setzt `previous_power` direkt, **umgeht Stufen 12–17** |
| 12 | PD / Feedforward / No-PD | 10335–10383 | `_compute_pd_new_power` 8815, darin Richtungshysterese 9001–9024 |
| 13 | Mixed-Fleet-Guards `apply_guards` | 10392 | `control/residual_load.py` |
| 14 | `_apply_zero_cross_hold` | 10407, Def. 9151 | Fenster `max(5 s, 2 × Aktor-Latenz)` |
| 15 | `_apply_min_power` | 10419, Def. 9045 | |
| 16 | `_apply_relay_dwell` | 10421, Def. 9080 | |
| 17 | Restriktionen: Zeitslots, Preisblock, EV, Solar-Surplus-Block | 10430–10488 | setzen auf 0, **nach** dem Dwell |
| 18 | Kapazitätsgrenzen, ICP-Clamp (Vertragsleistung) | 10495–10532 | ICP nutzt `sensor_filtered` |
| 19 | Verteilung auf Batterien, Schreiben, Zustand fortschreiben | 10600–10651 | |

## 1.2 Thema 1: Warum das Relais klappert

Deine Analyse stimmt in allen vier Punkten. Präzisierungen:

**Kernursache: Ein- und Ausschaltschwelle sind identisch.** Mit Min-Ladeleistung 50 W, Totband 40 W, Ziel 20 W, bezogen auf den Überschuss `S = -(error)`:

- Einschalten (`_apply_min_power`, 9063): `S ≥ min_charge − deadband` = **10 W**.
- Nach dem Einschalten mit 50 W ist `error = 50 − S`. Bei `S = 10` liegt der Fehler bei +40 W, also genau auf der Totbandkante.
- Ausschalten: Sobald `error > 40` (also `S < 10`), verlässt der Regler das Totband. Der PD rechnet `50 − 0,35·error < 50`, `_apply_min_power` liefert 0.

**Korrektur nach der Regelkreis-Simulation (AP A4):** Die Totband-Prüfung (Stufe 10) läuft vor `_apply_min_power`. Eine ruhende Batterie schaltet deshalb erst ein, wenn der Fehler das Totband verlässt (Überschuss-Fehler ≥ 40 W), und nicht schon bei 10 W. Die tatsächliche Hysterese von Upstream beträgt damit rund 30 W (ein bei 40 W, aus bei 10 W), nicht 0 W. Das ist schmaler als das Rauschen deines Sensors, deshalb klappert es trotzdem. Der Haupthebel sind die fehlenden Sperrzeiten, die Einschalthysterese ist nur eine Ergänzung.

**Ablauf eines Zyklus:** Aus-Wunsch → `_apply_relay_dwell` hält 15 s auf 50 W → 0 W (Bypass) → nächster Sample mit `S ≥ 10` → sofort wieder 50 W. Das ergibt genau das beobachtete Muster (15–40 s an, 2–3 s aus). Die 2–3 s sind Sensortakt plus Aktor-Latenz, keine Sperrzeit.

**Weitere bestätigte Punkte:**
- `_apply_relay_dwell` kennt nur aktiv → idle. Eine Mindest-Aus-Zeit existiert nicht.
- Der Hold entfällt bei `|error| − |previous_power| ≥ max(3·deadband, RELAY_COOLDOWN_HOLD_POWER=100)` (9123), bei dir 120 W.
- Laden ↔ Entladen wird nur durch die Richtungshysterese (betragsbasiert, 60 W) und das Zero-Cross-Fenster (5 s) gebremst. Beides begrenzt die Häufigkeit nicht.
- Alle Funktionen sind in `upstream/main` unverändert. Die Tags v1.6.1b1/b2 liegen auf einem anderen Zweig.

**Ergänzungen, die in deiner Analyse fehlen:**
- Vier Pfade schalten an jeder Haltezeit vorbei auf 0: Stufe 1 (Blocks), Stufe 9 (Peak-Shaving „conserving“), Stufe 17 (Restriktionen) und „keine Batterie verfügbar“ (10573). Das ist für Sicherheit richtig so, muss aber im Schaltzähler sichtbar werden.
- Der First-Execution-Pfad (Stufe 11) schaltet ohne Min-Power-, Zero-Cross- und Dwell-Prüfung ein. `first_execution = True` wird an sechs Stellen gesetzt (719, 5397, 6568, 6750, 6857, 9714), unter anderem nach Predictive-Wechseln und Dead-Meter-Recovery. Ob das im Normalbetrieb zum Klappern beiträgt, ist in AP A4 zu prüfen.
- Der Dwell hält bei 0-Wunsch auf Mindestleistung, auch wenn der PD eigentlich die Gegenrichtung will. Das ist gewollt (9099 ff.).

## 1.3 Thema 2: Datenfluss des Netzsensors

Es gibt genau einen Netzsensor: `entry.data["consumption_sensor"]` → `controller.primary_consumption_sensor` → Property `consumption_sensor` (1427, schaltet bei Off-Grid auf `CONF_OFFGRID_POWER_SENSOR`). Vorzeichen über `_apply_meter_transform` (4962) mit `meter_inverted`.

Deine Annahme stimmt: Peak Shaving rechnet `estimated_house_load = sensor_actual + excluded_adjustment − battery_power` (5179) gegen `capacity_protection_limit`. Mit dem synthetischen Sensor ist das falsch.

**Wichtige Korrektur: Es reicht nicht, nur die Erkennung umzustellen.** Peak Shaving wirkt, indem es den Sollwert des PD-Reglers überschreibt:

- Zweig „shaving“ (5190–5215): `set_setpoint_override("capacity_protection", limit)`. Der PD regelt danach **denselben Sensor** auf das Limit. Mit synthetischem Sensor regelt er also die falsche Größe auf 13 kW.
- Zweig „conserving“ (5216–5244): Sollwert = aktueller Sensorwert, Fehler 0, Batterie idle.
- Oberhalb der SOC-Schwelle ist Peak Shaving inaktiv. Der Regler geht davon aus, dass der normale Betrieb (Netz ≈ Ziel) den Peak ohnehin verhindert. Bei dir verhindert ihn in diesem Bereich nur dein Template (Term `erlaubt = min(peak·0,9, …)`).

Folge: Im „shaving“-Zweig muss auch die **Regelgröße** auf den realen Sensor wechseln.

**Zweite Korrektur, kritisch:** Stufe 3 hält den letzten Befehl, solange der Regelsensor keinen neuen Wert liefert. Ausnahme ist nur `capacity_protection_must_recheck` (10001), und das gilt nur bei laufender Entladung. Dein Sensor ist nachts genullt und damit konstant. Ein Lastanstieg am realen Zähler löst dann keinen Regelzyklus aus. Selbst wenn der Zyklus läuft, übernimmt 10338–10341 ohne neuen Regelwert einfach `previous_power`. Der zweite Sensor muss deshalb selbst Zyklen auslösen und im Peak-Fall als „neuer Messwert“ zählen.

### Alle Verbraucher des Netzsensors

| Stelle | Zweck | Künftig |
|---|---|---|
| `__init__.py` 9974 Hauptregelschleife | Regelgröße PD | **Regelsensor** |
| 2958 `_pd_house_demand_present` | Bedarf aus Sicht des PD | Regelsensor |
| `control/residual_load.py` 137 | Mixed-Fleet-Guards | Regelsensor |
| 9475–9760 Kadenz, Stale, Dead-Meter-Fallback | Gesundheit der Regelgröße | Regelsensor (realer Sensor erhält eigene, einfache Überwachung) |
| 12055–12091 State-Listener | löst Regelzyklen aus | Regelsensor **und** realer Sensor |
| 5133 `_apply_capacity_protection` | Hauslast gegen Peak-Limit, Regelgröße im Zweig „shaving“ | **realer Sensor** |
| 10524–10532 ICP-Clamp | Ladeleistung gegen Vertragsleistung | **realer Sensor** |
| 5316 `_apply_icp_excluded_protection` | nutzt nur die Differenz `sensor_filtered − sensor_actual` | unverändert |
| 10550–10567 Grid-at-Min-SOC-Zähler | Statistik | realer Sensor |
| `tracking/consumption_tracker.py` 1087, 1233, 1859 | Tages-Hausverbrauch, Netzbezug/Einspeisung, Solar-Erkennung | realer Sensor |
| `sensors/aggregate_sensors.py` 678 | Hausverbrauch-Leistung | realer Sensor |
| `__init__.py` 495–503 Panel `grid_entity` | Anzeige im Flussdiagramm | realer Sensor |
| `tracking/hourly_balance.py` 326 | Fallback, wenn kein externer Bilanzsensor gesetzt ist | realer Sensor |
| 6761 `_handle_predictive_grid_charging` | regelt Netzbezug Richtung Vertragsleistung | vorerst unverändert (nutzt du nicht; Phase 2) |
| `pricing/engine.py` 2582, `control/high_price_discharge.py` 577 | prüfen nur, ob der Zähler lebt (Curtailment) | vorerst unverändert (Phase 2) |
| `tracking/consumption_profile.py` 800, `switch.py` 1911, `infra/coordinator.py` 378 | Diagnose, Logging, reine Ablage | unverändert |

**Dreiphasen-Schutz:** `control/phase_power_limit.py` nutzt eigene Phasenstrom-Sensoren (`CONF_PHASE_x_CURRENT_SENSOR`) und nicht den Netzsensor. Deine Annahme „vermutlich betroffen“ trifft hier nicht zu. Das Modul ist außerdem ein Kandidat, um deine Phase-C-Grenze (4600 W) künftig nativ abzubilden, siehe Ausblick in 02.

## 1.4 Konfiguration und Tests, soweit für den Fork relevant

- `number`-Entitäten sind tabellengetrieben: `CONFIG_NUMBER_DEFINITIONS` in `const/integration_const.py` (ab ~1133). Ein neuer Parameter ist dort ein Dict-Eintrag plus Übersetzung.
- Der Controller liest die Werte im Konstruktor (z. B. 740) und beim Neuladen der Konfiguration (z. B. 3223). Beide Stellen müssen erweitert werden.
- Der Netzsensor wird in `config_flow.py` gewählt (Erstkonfiguration ~1604–1632, Optionen ~4136–4184).
- Config-Entry-`VERSION = 16` (`config_flow.py` 1485), Migrationen in `async_migrate_entry` (10823).
- Übersetzungen: `strings.json` plus `translations/{en,de,es,fr,nl,ca}.json`.
- Tests: 214 Dateien, reine Unit-Tests ohne `hass`-Fixture. Methoden werden ungebunden mit `SimpleNamespace`-Stubs aufgerufen (Muster: `tests/test_relay_dwell.py`, `tests/test_peak_shaving.py`, `tests/test_pd_min_power.py`). Erwartet wird Python 3.14. CI: `.github/workflows/tests.yml`.
- `domain_migration.py` existiert bereits (Umzug zwischen Domains ohne Verlust von `entity_id`). Für den Fork wird es nicht gebraucht, weil die Domain gleich bleibt.
- Auf diesem Rechner ist kein Python und kein `gh` installiert. `winget` ist vorhanden.
