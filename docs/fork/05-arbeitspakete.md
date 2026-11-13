# 5. Arbeitspakete und Modellzuteilung

## 5.1 Regeln für sparsamen Token-Verbrauch (Pro-Abo)

1. **Ein Arbeitspaket = eine neue Session.** Danach Session schließen. Kontext aus alten Paketen kostet bei jeder weiteren Nachricht erneut.
2. **`__init__.py` (12.497 Zeilen) nie ganz lesen lassen.** Jedes Paket nennt Funktionsnamen und Zeilenbereiche. Im Startprompt steht: „Lies nur die genannten Bereiche mit `offset`/`limit`.“
3. **Neue Logik in neuen, kleinen Dateien.** Das Modell arbeitet dann fast nur in Dateien unter 300 Zeilen.
4. **Tests vor Code.** Die Tests sind die Spezifikation. Das implementierende Modell braucht dann wenig Erklärung und kann selbst prüfen.
5. **Das implementierende Modell führt nur seine eigene Testdatei aus.** Die komplette Suite (214 Dateien, lange Ausgabe) läuft nur an Meilensteinen und immer mit Haiku.
6. **Starke Modelle nur für Eingriffe in die Regelschleife und für Reviews.** Opus bekommt dabei nur den Diff und die betroffenen Bereiche, nie das ganze Repo.
7. **Keine Subagenten und keine Workflows** in den Paket-Sessions. Jeder Subagent liest den Kontext neu ein.
8. Startprompt für jede Session: `Lies docs/fork/05-arbeitspakete.md, Abschnitt AP <Nr>, und die dort genannten Abschnitte. Setze genau dieses Paket um. Am Ende: Testdatei ausführen, Ergebnis nennen, einen Commit „AP <Nr>: <Titel>“ erstellen.`

## 5.2 Modellwahl

| Modell | Einsatz | Grund |
|---|---|---|
| **Haiku 5.5** | Einrichtung, Testläufe der ganzen Suite, Übersetzungen, Tabellen-Einträge, Manifest, Release-Schritte | mechanisch, lange Ausgaben, kein Entwurfsbedarf |
| **Sonnet 5.5** | Tests schreiben, neue Module implementieren, Config-Flow, Sensoren, einfache Hooks | Hauptarbeitstier, gutes Verhältnis von Qualität zu Verbrauch |
| **Opus 5.5** | Eingriff in die Stale-/Frische-Logik (B4), Review der beiden Meilenstein-Diffs | Fehler dort wirken direkt auf die Batterieregelung und sind schwer zu finden |
| **Fable 5.2** | nicht eingeplant | Architektur ist mit diesem Konzept erledigt. Nur bei einem Problem einsetzen, an dem Opus scheitert. |

Aufwand: S = unter 15 Minuten Sessionzeit, M = eine fokussierte Session, L = eine volle Session, eventuell an das Nutzungslimit heranreichend.

## 5.3 Übersicht

| AP | Titel | Modell | Aufwand | Hängt ab von |
|---|---|---|---|---|
| 0 | Einrichtung, Basislinie | Haiku + du | S | – |
| A1 | Tests RelayGuard (Logik) | Sonnet | M | 0 |
| A2 | `relay_guard.py` implementieren | Sonnet | M | A1 |
| A3 | Konfiguration Guard (Konstanten, Numbers, Switch, Übersetzungen) | Haiku | M | 0 |
| A4 | Hooks in der Regelschleife, Regelkreis-Simulation | Sonnet | L | A2, A3 |
| A5 | Diagnosesensor | Sonnet | S | A4 |
| M1 | Review, Suite, Release `v1.6.0.1`, Umstieg, Feldtest | Opus (Review), Haiku (Rest), du | M | A5 |
| B1 | Tests GridView und Peak Shaving mit zwei Sensoren | Sonnet | M | M1 |
| B2 | `grid_view.py`, Options-Flow, dynamisches Limit | Sonnet | L | B1 |
| B3 | Peak Shaving und ICP-Clamp auf realen Sensor (P1–P3) | Sonnet | M | B2 |
| B4 | Listener und Frische-Logik (P4, P5) | **Opus** | L | B3 |
| B5 | Statistik und Anzeige auf realen Sensor (P6) | Sonnet | S | B2 |
| M2 | Review, Suite, Release `v1.6.0.2`, Feldtest | Opus (Review), Haiku (Rest), du | M | B4, B5 |
| B6 | optional: Peak-Deckel oberhalb der SOC-Schwelle | Sonnet | S | M2 |
| C1 | Übersicht auf Systemstatus reduzieren | Sonnet | S | M1 |
| C2 | optional: nicht benötigte Entitäten deaktivieren | Haiku + du | S | C1 |
| U1 | optional: Upstream-Issue und PR für den Guard | Sonnet | M | Feldtest M1 |

Thema 1 (A) und Thema 2 (B) sind unabhängig. Die Reihenfolge A vor B ist gewählt, weil A weniger Risiko hat und dein akutes Problem löst. C1 kann jederzeit nach M1 eingeschoben werden.

## 5.4 Die Pakete im Detail

### AP 0: Einrichtung und Basislinie (Haiku + du)

Von dir:
- GitHub: `ffunes/Omnibattery` forken (Weboberfläche), URL nennen.
- Python 3.14 installieren: `winget install Python.Python.3.14`.

Haiku:
1. `git remote add origin <Fork-URL>`, `git push -u origin fork/main`.
2. `py -3.14 -m venv .venv-test`, `pip install -r requirements-test.txt`, `.venv-test` in `.git/info/exclude` eintragen.
3. Komplette Suite laufen lassen, Zusammenfassung nach `docs/fork/baseline-tests.txt`.
4. `hacs.json`, `manifest.json`, Workflows laut 06, Abschnitt 6.2 anpassen.
5. Upstream ignoriert `docs/` (`.gitignore` Zeile 32). Das Konzept mit `git add -f docs/fork` aufnehmen und committen, damit es im Fork liegt.

Fertig, wenn: Basislinie dokumentiert, Fork auf GitHub, CI-Lauf `tests.yml` grün oder Abweichungen notiert.

### AP A1: Tests RelayGuard (Sonnet)

Lesen: 02 Abschnitt 2.1, 04 Abschnitt 4.1, als Stilvorlage `tests/test_relay_dwell.py` (154 Zeilen).
Erstellen: `tests/test_relay_guard.py` mit G1–G21.
Fertig, wenn: Datei sammelt fehlerfrei ein und alle Tests schlagen mit `ImportError` bzw. `ModuleNotFoundError` fehl (Modul existiert noch nicht).
Nicht anfassen: alles unter `custom_components/`.

### AP A2: `relay_guard.py` (Sonnet)

Lesen: 02 Abschnitt 2.1, `tests/test_relay_guard.py`.
Erstellen: `custom_components/omnibattery/control/relay_guard.py` (Ziel unter 250 Zeilen, keine HA-Imports).
Fertig, wenn: G1–G21 grün. Tests werden nicht verändert. Widerspricht ein Test der Spezifikation in 02, anhalten und nachfragen.

### AP A3: Konfiguration Guard (Haiku)

Lesen: 03 Abschnitt 3.1 und 3.4. In `const/integration_const.py` die Bereiche um `CONF_PD_RELAY_COOLDOWN` (Zeilen ~675–746 und ~1200–1222). Per Suche: wie `pd_relay_cooldown` in `strings.json`, `translations/en.json`, `translations/de.json` und in `switch.py` ein vergleichbarer einfacher Konfigurationsschalter eingetragen ist.
Ändern: Konstanten und Defaults, 5 neue Number-Definitionen, Maximum von `pd_relay_cooldown` auf 600, Schalter `pd_relay_guard_enabled`, Übersetzungen (en, de, Rest englisch).
Tests: C1 und C3 aus 04 in `tests/test_relay_guard_config.py`.
Fertig, wenn: neue Tests grün, `tests/test_marstek_number.py` und `tests/test_entity_naming.py` grün.
Falls das Muster für den Schalter nicht eindeutig ist: anhalten, Fundstellen nennen. Dann übernimmt Sonnet den Schalter in A4.

### AP A4: Hooks und Regelkreis-Simulation (Sonnet)

Lesen: 01 Abschnitt 1.1 und 1.2, 02 Abschnitt „Hook-Punkte“, 04 Abschnitt 4.2. In `__init__.py` nur: 730–745, 3215–3230, die ersten 40 Zeilen von `async_update_charge_discharge`, 10400–10430, sowie die Definitionen `_apply_min_power` und `_apply_relay_dwell` (9045–9150).
Ändern: vier Hooks laut Tabelle, jeweils mit Kommentar `# FORK:`. `peak_bypass` = `self._capacity_protection_active`.
Erstellen: `tests/test_relay_guard_closed_loop.py` mit S1–S7.
Zusätzlich klären und im Commit-Text festhalten: Wird `first_execution` im Normalbetrieb regelmäßig neu gesetzt (Stellen 6568, 6750, 6857, 9714)? Wenn ja, als offenen Punkt melden, nicht selbst lösen.
Fertig, wenn: S1–S7 grün, `tests/test_relay_dwell.py`, `test_pd_min_power.py`, `test_pd_zero_cross.py`, `test_pd_direction_hysteresis.py` unverändert grün.

### AP A5: Diagnosesensor (Sonnet)

Lesen: 02 Abschnitt „Diagnose“. Per Suche in `sensor.py` einen vorhandenen einfachen Controller-Diagnosesensor als Vorlage finden (z. B. den Status-Sensor zu Peak Shaving über `_capacity_protection_status`).
Ändern: Sensor registrieren, Übersetzungen.
Fertig, wenn: ein Test prüft Zustand und Attribute gegen einen Controller-Stub.

### M1: Meilenstein Thema 1

1. **Opus:** Review von `git diff v1.6.0..fork/main -- custom_components/` gegen 02 Abschnitt 2.1. Auftrag: Kann der Guard Leistung erzwingen oder eine Schutzfunktion verzögern? Sind die Hooks minimal? Nur Befunde, keine Umbauten.
2. **Sonnet:** Befunde beheben (nur falls vorhanden).
3. **Haiku:** komplette Suite, Vergleich mit der Basislinie, Version `1.6.0.1`, Tag, Push, GitHub-Release.
4. **Du:** Umstieg laut 06 Abschnitt 6.3, Parameter setzen, 3 Tage Feldtest (04 Abschnitt 4.6).

### AP B1: Tests Thema 2 (Sonnet)

Lesen: 02 Abschnitt 2.2, 04 Abschnitte 4.3 und 4.4, `tests/test_peak_shaving.py` (Stub `_controller`).
Erstellen: `tests/test_grid_view.py` (V1–V11), `tests/test_peak_shaving_physical.py` (K1–K6, K8, K9). K7 folgt in B4.
Fertig, wenn: Tests sammeln ein und schlagen erwartungsgemäß fehl.

### AP B2: `grid_view.py`, Options-Flow, dynamisches Limit (Sonnet)

Lesen: 02 Abschnitt 2.2 (Schnittstelle, Regeln), 03 Abschnitt 3.2. In `__init__.py`: `_apply_meter_transform` (4962–4992) als Vorlage für Einheiten und Vorzeichen, `_check_missing_configured_sensors` (9315–9373) für den Repair-Eintrag. In `config_flow.py`: 4130–4200.
Erstellen: `control/grid_view.py`. Ändern: Options-Flow (3 Felder), Konstanten, Number `capacity_protection_limit_margin`, Instanz im Controller, Liste der überwachten Sensoren, Übersetzungen.
Fertig, wenn: V1–V11 grün, `tests/test_configured_sensor_missing.py` grün.

### AP B3: Peak Shaving und ICP-Clamp (Sonnet)

Lesen: 01 Abschnitt 1.3, 02 Tabelle P1–P3. In `__init__.py`: 5133–5314, 10108–10121, 10517–10532.
Ändern: P1, P2, P3. Der Aufruf in 10119 übergibt `physical_actual`.
Fertig, wenn: K1–K6 grün, `tests/test_peak_shaving.py`, `test_peak_shaving_switch.py`, `test_icp_excluded_protection.py` unverändert grün.

### AP B4: Listener und Frische-Logik (Opus)

Lesen: 01 Abschnitt 1.3 („Zweite Korrektur“), 02 Tabelle P4 und P5. In `__init__.py`: 9418–9470 (`_track_control_sample`, `_track_sensor_report`), 9983–10072, 10335–10345, 12040–12100. Tests als Kontext: `tests/test_control_sample_scheduling.py`, `tests/test_non_responsive_sensor.py`.
Ändern: P4 und P5 mit möglichst wenigen Zeilen. `peak_review_pending` als eigene kleine Methode, damit sie testbar ist.
Tests: K7 ergänzen. Zusätzlich ein Test, dass ohne zweiten Sensor der Ausdruck immer `False` ist.
Fertig, wenn: neue Tests grün und `test_control_sample_scheduling.py`, `test_control_cycle_scheduling.py`, `test_non_responsive_sensor.py`, `test_dead_main_sensor.py`, `test_pd_zero_cross.py` unverändert grün.
Abbruchkriterium: Braucht die Lösung mehr als etwa 40 geänderte Zeilen in der Schleife, anhalten und die Alternative beschreiben (z. B. Peak-Fall als eigener kurzer Regelpfad vor der Frische-Prüfung).

### AP B5: Statistik und Anzeige (Sonnet)

Lesen: 01 Tabelle „Alle Verbraucher“, Zeilen mit „realer Sensor“ außer den in B3 erledigten.
Ändern: Methode `read_statistics_grid_w()` im Controller, Aufrufer in `tracking/consumption_tracker.py` (1087, 1233, 1859), `sensors/aggregate_sensors.py` (678), `tracking/hourly_balance.py` (326), Panel-Konfiguration (`__init__.py` 495–503), Grid-at-Min-SOC (10550–10561).
Fertig, wenn: `tests/test_consumption_tracker.py`, `test_hourly_balance.py`, `test_aggregate_power.py`, `test_grid_at_min_soc.py`, `test_panel_config.py` grün.
Hinweis für dich: Die Tageswerte Netzbezug und Einspeisung springen ab diesem Zeitpunkt auf reale Werte. Das ist gewollt.

### M2: Meilenstein Thema 2

Wie M1. Review-Auftrag an Opus: Verhalten ohne zweiten Sensor bitgenau wie vorher? Kann ein veralteter realer Sensor die Batterie in eine Dauerentladung treiben? Version `1.6.0.2`.

### AP B6 (optional): Peak-Deckel oberhalb der SOC-Schwelle (Sonnet)

Lesen: 02 Abschnitt „Optional: Peak-Deckel“. Ändern: Schalter, etwa 10 Zeilen nach dem Aufruf von `_apply_icp_excluded_protection`, Tests K8 und K9. Erst nach erfolgreichem Feldtest M2.

### AP C1: Übersicht auf Systemstatus reduzieren (Sonnet)

Lesen: 02 Abschnitt 2.3. In `frontend/marstek-panel.js` nur `_renderResumen` (ab Zeile 2545, mit `offset`/`limit` in Blöcken von 200 Zeilen bis zum Ende der Methode). Die Datei hat 556 KB, niemals ganz lesen.
Ändern: Schalter `panel_minimal_overview` (Default aus), Weitergabe über `panel_config`, in `_renderResumen` bei gesetztem Schalter nur den Systemstatus rendern.
Fertig, wenn: `tests/test_panel_config.py` und die `test_frontend_*`-Tests grün, Sichtprüfung im Browser durch dich.

### AP C2 (optional): Entitäten deaktivieren (Haiku + du)

Kein Code. Haiku listet über den Home-Assistant-Zugang die Omnibattery-Entitäten auf, die nur die ausgeblendeten Karten bedienen (Tages- und Wochenenergie, Tagesbetrieb-Zeitleiste) und in keiner Automation oder keinem Dashboard vorkommen. Du entscheidest, welche deaktiviert werden.

### AP U1 (optional): Upstream (Sonnet)

Issue-Text auf Englisch mit Messdaten vor und nach dem Guard, Branch von `upstream/main` mit `relay_guard.py`, Hooks, Tests, ohne `# FORK:`-Kommentare und ohne Schaltbudget. Einreichen erst nach deiner Freigabe.

## 5.5 Risiken

| # | Risiko | Gegenmaßnahme |
|---|---|---|
| R1 | Eingriff in die Frische-Logik (B4) erzeugt Aufschwingen oder blockiert den Regler | Opus, eigenes Abbruchkriterium, bestehende Scheduling-Tests als Schutz, Feldtest mit gezieltem Peak |
| R2 | Realer Sensor fällt aus und Peak Shaving reagiert falsch | Fallback auf den Regelsensor (heutiges Verhalten), Altersgrenze 60 s, Repair-Eintrag |
| R3 | Guard verhindert erwünschtes Laden bei kleinem Überschuss | Schalter und alle Zeiten einzeln abschaltbar, Diagnosesensor zeigt den Grund |
| R4 | Upstream baut die Pipeline um, Hooks passen nicht mehr | wenige, mit `# FORK:` markierte Hooks, Rebase nur auf stabile Tags |
| R5 | Simulation S1 reproduziert das Klappern nicht | Dann stimmt das Modell nicht: Parameter prüfen, notfalls echte Sensordaten aus der HA-Historie als Eingang verwenden |
| R6 | Marstek schaltet das Relais unabhängig vom Befehl (Firmware) | Im Feldtest Schaltzähler des Guards mit den Statuswechseln des Wechselrichters vergleichen |
| R7 | Nutzungslimit mitten im Paket | Jedes Paket endet mit einem Commit. Bei Abbruch: neue Session, „AP x fortsetzen, `git status` und `git diff` ansehen“. |

## Offene Fragen

1. **Realer Netzsensor:** Welche Entität soll als realer Bezug gelten? Nach deinem Template wäre es `bezug_kbh − export + bezug_papa`. Zählt der EEG-Anteil (`bezug_papa`) bei der Peak-Abrechnung mit, oder nur der Bezug am eigenen Zähler? Davon hängt ab, ob du einen kleinen Template-Sensor dafür anlegst oder einen vorhandenen Zähler nimmst. Blockiert erst B-Pakete.
2. **Tote Zone:** Mit Einschalthysterese wird Überschuss zwischen 10 W und 50 W nicht mehr geladen, solange die Batterie aus ist. Einverstanden? Alternative: Min-Ladeleistung auf 30 W senken.
3. **GitHub:** Fork öffentlich unter deinem Konto in Ordnung?
4. **Python 3.14 lokal installieren** in Ordnung? Alternative: Tests nur über GitHub Actions, dann dauert jede Prüfrunde einige Minuten länger und Haiku liest die Ergebnisse von GitHub.
5. **Panel:** Soll der Menüpunkt „Omnibattery“ in der Seitenleiste bleiben (mit reduzierter Übersicht), oder reicht dir der Systemstatus als normale Entitäten in deinem eigenen Dashboard? Im zweiten Fall ist C1 überflüssig.
