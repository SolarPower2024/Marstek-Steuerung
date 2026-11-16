# 8. Regelzyklus-Trace

Stand 16.11.2026. Diagnose für das Relais-Klappern (Discharge↔None im 2–4-s-Takt, siehe `log-analyse-2026-11-16-nachmittag.md`). Der Trace schreibt pro Regelzyklus eine Zeile mit allen Zwischenwerten der Befehlskette, dazu einzelne Ereigniszeilen. Er greift nicht in die Regelung ein: Ist er aus, wird nichts aufgezeichnet. Fehler beim Aufzeichnen werden verschluckt.

Code: `custom_components/omnibattery/infra/cycle_trace.py`, Messpunkte mit `# FORK: trace` in `__init__.py` und `control/residual_load.py`. Tests: `tests/test_cycle_trace.py`.

## 8.1 Ein- und ausschalten

Eigener Logger, unabhängig vom Level der Integration (die kann auf WARNING bleiben):

```yaml
# Entwicklerwerkzeuge → Aktionen
action: logger.set_level
data:
  custom_components.omnibattery.trace: debug
```

Ausschalten mit `warning` statt `debug`. Nach einem Neustart von Home Assistant ist er wieder aus, außer er steht dauerhaft in `configuration.yaml` unter `logger: logs:`.

## 8.2 Wohin geschrieben wird

1. **Eigene Datei** `/config/omnibattery_trace/trace.log`, rotierend 10 × 10 MB (`trace.log.1` … `.10`). Bei einem Zyklus alle 1–2 s reicht das für etwa 1–2 Tage.
2. **Home-Assistant-Log**, Suche nach `omnibattery.trace`.

Damit Claude die Datei direkt lesen kann: in den ha-mcp-Einstellungen den Ordner `omnibattery_trace` als zusätzliches Verzeichnis freigeben. `www/` ist dafür ungeeignet, weil es ohne Anmeldung unter `/local/` erreichbar ist.

## 8.3 Zeilenarten

| Art | Wann |
|---|---|
| `CYC` | eine pro Regelzyklus, am Ende geschrieben |
| `WRITE` | jeder echte Registerschreibvorgang (Befehl hat sich geändert), auch außerhalb des Regelzyklus |
| `SG_LATCH` / `SG_RELEASE` | Surplus guard rastet ein bzw. gibt frei |

## 8.4 Felder der `CYC`-Zeile

Vorzeichen: Leistung + = Laden, − = Entladen. Netz + = Bezug.

| Feld | Bedeutung |
|---|---|
| `cyc` | laufende Zyklusnummer (auch in `WRITE`/`SG_*`) |
| `prev_p`, `new_p` | Befehl vor und nach dem Zyklus |
| `out_sign`, `zc_armed`, `dwell_armed` | letzte Richtung, Zero-Cross-Hold scharf, Relais-Dwell scharf |
| `sg_latched`, `sg_latched_end` | Surplus guard eingerastet vor/nach dem Zyklus |
| `bat` | je Batterie `soc/bp(battery_power)/ac(ac_power)/acd(ac_delivered)/inv(inverter_state)/fm(force_mode)/sc/sd(Sollwerte)/av(verfügbar)/man(manuell)` |
| `raw`, `new`, `el`, `stale`, `paced` | Regelsensor roh, neuer Messwert ja/nein, Abstand in s, veraltet, gedrosselter Tick |
| `phys`, `phys_new` | realer Netzsensor (Peak Shaving) |
| `recalc`, `cp_recheck`, `blk_changed`, `phase_pend`, `peak_pend` | Gründe, warum ein Zyklus ohne neuen Messwert trotzdem rechnet |
| `filt` | Regelsensor nach EMA-Filter |
| `man`, `excl`, `icp` | Abzüge: manuelle Batterieladung, ausgeschlossene Geräte, ICP-Schutz |
| `act` | `sensor_actual`, die Größe, auf die PD **und** Guards rechnen |
| `tgt`, `sp`, `db` | aktiver Sollwert, seine Bestandteile, Totband |
| `err`, `law` | Regelabweichung `act − tgt`; Regelgesetz `pd`/`ff`/`nopd`/`hold` |
| `p_pd` | Ausgabe des Regelgesetzes |
| `unc` | je Aufruf: `grid + Batterie:Quelle:Beitrag = uncovered` |
| `sg` | je Aufruf: `grid/tgt/dem(residual demand)/band/vorher>nachher/pend(Sekunden in Wartezeit)` |
| `ag` | je Aufruf: `Anfrage>nach Feedforward>nach Surplus guard>nach Deckel` |
| `p_guard`, `p_zc`, `p_min`, `p_dwell`, `p_rg` | Befehl nach Guards, Zero-Cross-Hold, Mindestleistung, Relais-Dwell, Relais-Schutz |
| `rg` | Entscheidung des Relais-Schutzes `Grund:Restzeit` |
| `restricted`, `cblk`, `dblk` | Sperren aktiv, Lade-/Entladesperren |
| `p_final`, `alloc` | geschriebener Befehl, Aufteilung je Batterie |
| `exit` | wo der Zyklus endete: `written`, `deadband`, `keep_last`, `no_available`, `cp_force_idle`, `first_exec`, `manual_mode`, `max_soc`, `dead_meter`, `predictive`, `charge_block`, `discharge_block`, `no_sensor`, sonst `early` |
| `ms`, `exc`, `trace_err` | Laufzeit, Ausnahme im Zyklus, Fehler beim Aufzeichnen |

`unc`, `sg` und `ag` sind Listen, weil die Guards pro Zyklus bis zu dreimal laufen: in `guards_pending` mit dem **Rohwert** (Kurzschluss ohne neuen Messwert), in `guards_pending` mit `sensor_actual` (Totband) und in der eigentlichen Kette.

## 8.5 Auswertung des Klapperns

Für jeden Flip `WRITE … net=-30` → `WRITE … net=0`:

1. Welches `p_*`-Feld setzt die 0? Erwartet nach dem Befund vom 16.11.: `p_guard`.
2. Stimmen `err` (PD) und `dem` (Guard) im Vorzeichen überein? Wenn nicht: Unterschied = Summe der `ac`-Beiträge in `unc`. Passt der `ac`-Wert zu `bp` und zum tatsächlichen Befehl `sd`?
3. Was hat den PD vorher zur Entladung gebracht (`law`, `err`, `p_pd`, `p_min`, `p_dwell`)?
4. Wie lange war `sg` eingerastet, und was hat `SG_RELEASE` ausgelöst (`demand` > 0)?
