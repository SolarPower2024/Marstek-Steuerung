# 7. Stand der Umsetzung und Inbetriebnahme

Stand 14.11.2026. Dieses Dokument beschreibt, was tatsächlich gebaut wurde. Wo es von 01–06 abweicht, gilt dieses Dokument.

## 7.1 Branches

| Branch | Basis | Manifest-Version | Tests |
|---|---|---|---|
| `main` (lokal `fork/main`) | Upstream-Tag `v1.6.0` | 1.6.0.1 | 4123 bestanden, 0 fehlgeschlagen |
| `fork/v2.0.0` | Upstream-Zweig `release/v2.0.0` (unveröffentlicht, Stand 12.11.2026) | 2.0.0.1 | 4546 bestanden, 0 fehlgeschlagen |

Die Fork-Commits ließen sich ohne inhaltlichen Konflikt auf den 2.0.0-Zweig übertragen. Empfehlung: `main` installieren, bis Upstream 2.0.0 veröffentlicht.

## 7.2 Abweichungen vom Konzept

- **Ein Durchgang:** Thema 1 und Thema 2 sind zusammen umgesetzt, keine getrennten Meilensteine.
- **Hysterese:** Upstream hat bereits rund 30 W Hysterese (Totband-Prüfung vor `_apply_min_power`). Die Wirkung des Relais-Schutzes kommt überwiegend aus den Sperrzeiten. Siehe Korrekturen in 01 und 02.
- **Schaltbudget:** harte Grenze statt Faktor-3-Drossel. Zustand `budget` im Diagnosesensor.
- **Diff in `__init__.py`:** rund 400 Zeilen statt der geplanten 75. Der größte Teil sind neue Hilfsmethoden, in der Regelschleife selbst sind es etwa 60 Zeilen. Alle Stellen sind mit `# FORK:` markiert.
- **Stundenbilanz** bleibt auf dem Regelsensor (Review-Befund 3): Sie verschiebt den Sollwert und ist keine reine Statistik.
- **Nach dem Review ergänzt:**
  - Eingefrorener realer Zähler: Ändert sich der Wert während einer Peak-Entladung 60 s nicht (`PHYSICAL_FROZEN_S`), gilt er als unbrauchbar, bis er sich wieder ändert.
  - Exit-Hold: Beim Wechsel von „shaving“ auf „conserving“ wird erst nach 6 s (`PEAK_EXIT_HOLD_S`) abgeschaltet.
  - Limit-Entität außerhalb 500–20.000 W wird verworfen statt geklemmt.
  - Der reale Sensor löst nur Regelschritte aus, wenn Peak Shaving eingeschaltet und SOC-begrenzt oder aktiv ist.
- **Nicht umgesetzt:** B6 (Peak-Deckel oberhalb der SOC-Schwelle), C2 (Entitäten deaktivieren), U1 (Upstream-PR). Der First-Execution-Pfad schaltet weiterhin am Relais-Schutz vorbei (nur bei Start, Predictive-Wechsel, Sensorausfall).

## 7.3 Neue Entitäten und Optionen

| Art | Name (de) | Schlüssel |
|---|---|---|
| Schalter | Relais-Schutz | `pd_relay_guard_enabled` |
| Schalter | Reduzierte Übersicht | `panel_minimal_overview` |
| Zahl | Relais-Mindest-Aus-Zeit | `pd_relay_min_off` |
| Zahl | Sperrzeit Richtungswechsel | `pd_direction_lock` |
| Zahl | Einschaltabstand zur Mindestleistung | `pd_engage_margin` |
| Zahl | Sperren aufheben ab Abweichung | `pd_guard_bypass_error` |
| Zahl | Schaltbudget pro Stunde | `pd_relay_switch_budget` |
| Zahl | Sicherheitsabstand Peak-Limit | `capacity_protection_limit_margin` |
| Zahl | Entladehysterese Kapazitätsschutz | `capacity_protection_soc_hysteresis` |
| Sensor (Diagnose) | Relais-Schutz | Zustand = Grund, Attribute Restzeit und Schaltzähler |
| Sensor (Diagnose) | Peak-Netzquelle | `physical` / `fallback` / `none` |
| Options-Flow, Schritt Sensoren | realer Netzsensor, Vorzeichen umkehren, Entität für Peak-Limit | `physical_grid_sensor`, `physical_grid_inverted`, `capacity_protection_limit_entity` |

## 7.4 Empfohlene Einstellungen für den Start

| Parameter | Wert |
|---|---|
| Relais-Mindestlaufzeit (`pd_relay_cooldown`) | 60 s |
| Relais-Mindest-Aus-Zeit | 60 s |
| Sperrzeit Richtungswechsel | 90 s |
| Einschaltabstand | 0 W (bei weiterem Klappern 20–30 W) |
| Sperren aufheben ab Abweichung | 200 W |
| Schaltbudget pro Stunde | 30 |
| Min. Ladeleistung | 30 W (deine Entscheidung) |
| Relais-Schutz | ein |
| Sicherheitsabstand Peak-Limit | 10 % (ersetzt den Faktor 0,9 aus dem Template, wenn die Limit-Entität gesetzt ist) |
| Entladehysterese Kapazitätsschutz | 2 % |

Für den ersten Test des Relais-Schutzes kann Peak Shaving ausgeschaltet oder der reale Sensor leer bleiben. Beide Funktionen sind unabhängig.

## 7.5 Feldtest

Relais-Schutz (3 Tage):
1. Statuswechsel des Wechselrichters in der Historie zählen. Referenz: 242 in 3 Tagen. Ziel: unter 40.
2. `switches_today` des Diagnosesensors mit der Historie vergleichen. Weichen sie stark ab, schaltet die Batterie unabhängig vom Befehl.
3. Bei einem großen Lastsprung muss der Zustand kurz `bypass_error` zeigen und die Batterie sofort reagieren.

Zweiter Netzsensor:
1. „Peak-Netzquelle“ muss `physical` zeigen. Zeigt sie häufig `fallback`, meldet der Sensor zu selten (Altersgrenze 60 s). Dann einen Zähler wählen, der regelmäßig meldet.
2. Peak-Fall erzeugen: SOC-Schwelle vorübergehend über den aktuellen SOC setzen, Limit niedrig wählen, großen Verbraucher einschalten. Erwartung: Entladung beginnt innerhalb weniger Sekunden, der reale Bezug pendelt sich am Limit ein.
3. Verbraucher ausschalten. Erwartung: Entladung endet nach etwa 6 s, im Log steht „Capacity Protection conserving capacity“.
4. Nachts mit genulltem Regelsensor wiederholen.
5. Tageswerte Netzbezug und Einspeisung springen auf reale Werte. Das ist gewollt.

Reduzierte Übersicht: Schalter einschalten, Browser neu laden. Es darf nur noch die Karte „Systemstatus“ erscheinen.

## 7.6 Bekannte Restrisiken

- Zähler, die nur bei Änderung melden (Template, ESPHome mit `delta`), fallen nach 60 s Ruhe auf den Regelsensor zurück. Im Peak-Fall endet die Entladung dann 6 s später.
- Während des Exit-Holds entlädt die Batterie bis zu 6 s auf altem Niveau weiter.
- Listener und Repair-Hinweis sind nur in Unit-Tests geprüft, nicht in einer laufenden Home-Assistant-Instanz.
- Mit ausgeschlossenen Geräten und realem Sensor zugleich ist der Zweig „conserving“ nicht eigens geprüft.
