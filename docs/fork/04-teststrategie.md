# 4. Teststrategie

Stil wie Upstream: reine Unit-Tests, keine `hass`-Fixture, Methoden ungebunden mit `SimpleNamespace`-Stubs. Zeit wird in den neuen Modulen als Zahl übergeben, es braucht kein Freeze-Time.

Ausführen (nach AP 0):

```bash
.venv-test\Scripts\python -m pytest tests/test_relay_guard.py -q
```

## 4.1 `tests/test_relay_guard.py` (reine Logik)

Standardwerte, falls nicht anders angegeben: `min_off_s=60`, `direction_lock_s=90`, `engage_margin_w=0`, `bypass_error_w=200`, `budget_per_hour=0`, Min-Laden 50 W, Min-Entladen 30 W. Vorzeichen: Leistung + = Laden, `error` + = Bezug über Ziel.

| # | Szenario | Erwartung |
|---|---|---|
| G1 | Guard deaktiviert, beliebige Anforderung | unverändert durchgelassen, `reason="disabled"` |
| G2 | idle, Anforderung +50, `error=−30` | 0, `engage_threshold` (Bedarf 30 < 50) |
| G3 | idle, Anforderung +50, `error=−50` | +50, `pass` |
| G4 | idle, Anforderung −30, `error=+29` / `+30` | 0 / −30 |
| G5 | `engage_margin_w=20`, `error=−60` / `−70` | 0 / +50 |
| G6 | Min-Leistung 0 konfiguriert | Einschalthysterese greift nicht |
| G7 | Abschalten bei t=0, Einschaltwunsch bei t=30 | 0, `min_off`, `remaining_s≈30` |
| G8 | wie G7, t=61 | durchgelassen |
| G9 | wie G7, aber `error=−250` | durchgelassen, `bypass_error` |
| G10 | wie G7, `peak_bypass=True` | durchgelassen, `bypass_peak` |
| G11 | Laden endet bei t=0, Entladewunsch bei t=70 (min_off abgelaufen) | 0, `direction_lock`, `remaining_s≈20` |
| G12 | wie G11, t=91 | durchgelassen |
| G13 | Laden endet bei t=0, erneuter Ladewunsch bei t=70 | durchgelassen (gleiche Richtung, keine Richtungssperre) |
| G14 | aktiv Laden seit 20 s, direkter Entladewunsch | 0, `direction_lock` |
| G15 | aktiv Laden seit 100 s, direkter Entladewunsch mit ausreichendem Bedarf | durchgelassen |
| G16 | aktiv Laden, Anforderung 0 oder +200 | unverändert durchgelassen |
| G17 | `stale_recalc=True` während Sperre | durchgelassen, Zeitstempel und Zähler unverändert |
| G18 | `budget_per_hour=4`, 4 Schaltvorgänge in 10 min, Einschaltwunsch 100 s nach dem Abschalten | 0 (Sperrzeit 180 s statt 60 s), `throttled=True` |
| G19 | wie G18, 61 min später | Zähler 0, normale Sperrzeit |
| G20 | `observe` mit Folge 0, 50, 50, 0, −30, 0 | 4 Schaltvorgänge, `last_switch` gesetzt |
| G21 | `status()` | enthält alle Attribute aus 02, Abschnitt Diagnose |

## 4.2 `tests/test_relay_guard_closed_loop.py` (Regelkreis-Simulation)

Kleines Streckenmodell im Test: `netz = last − pv + batterie`, Batterie folgt dem Befehl nach einem Sample. Regler: die echten ungebundenen Methoden in Pipeline-Reihenfolge (`_apply_min_power` → `_apply_relay_dwell` → `RelayGuard.filter`), vorgeschaltet ein vereinfachter P-Schritt (`previous − kp·error`). Parameter wie in deinem Setup (Kp 0,35, Totband 40, Ziel 20, Min 50/30). Sample-Abstand 2 s, simulierte Dauer 30 min.

| # | Szenario | Erwartung |
|---|---|---|
| S1 | **Referenz ohne Guard:** Überschuss 30 W ± 25 W Rauschen (fester Seed), Dwell 15 s | mehr als 20 Schaltvorgänge. Der Test belegt, dass die Simulation das Klappern reproduziert. |
| S2 | wie S1, Guard an (60/90/0/200), Dwell 60 s | höchstens 4 Schaltvorgänge |
| S3 | Überschuss konstant 80 W | genau 1 Einschaltvorgang, danach dauerhaft Laden |
| S4 | Überschuss wechselt alle 45 s zwischen +80 W und −80 W | höchstens 1 Richtungswechsel pro 90 s |
| S5 | Lastsprung +1500 W bei aktiver Min-Aus-Sperre | Entladebefehl im nächsten Sample |
| S6 | Guard an, Überschuss 0 → 300 W Rampe über 10 min | Batterie schaltet einmal ein und folgt, kein Abschalten |
| S7 | Budget 6/h, Szenario S4 über 60 min | Schaltvorgänge ≤ 12, `throttled` war zeitweise `True` |

## 4.3 `tests/test_grid_view.py`

| # | Szenario | Erwartung |
|---|---|---|
| V1 | kein zweiter Sensor | `has_physical=False`, `read_physical` → `None` |
| V2 | Sensor 1500 W, nicht invertiert / invertiert | 1500 / −1500 |
| V3 | Zustand `unavailable`, `unknown`, Text, Entität fehlt | `None` |
| V4 | letzter Bericht 61 s alt | `None` |
| V5 | Einheit kW | Umrechnung in W (wie `_apply_meter_transform`) |
| V6 | gleicher Wert zweimal / neuer Wert | `physical_is_new` `False` / `True` |
| V7 | Limit-Entität 12000, Marge 10 % | 10800 |
| V8 | Limit-Entität 100 / 50000 | 500 / 20000 |
| V9 | Limit-Entität unbrauchbar, letzter gültiger Wert 5 min alt | letzter gültiger Wert |
| V10 | wie V9, 16 min alt | statischer Zahlenwert |
| V11 | keine Limit-Entität | statischer Zahlenwert, ohne Marge-Abzug nur wenn Marge 0 |

## 4.4 Erweiterung `tests/test_peak_shaving.py` und neue `tests/test_peak_shaving_physical.py`

Vorhandene Tests müssen unverändert grün bleiben (Beweis der Rückwärtskompatibilität).

| # | Szenario (SOC unter Schwelle, Limit 3000 W) | Erwartung |
|---|---|---|
| K1 | Regelsensor 0 W (synthetisch genullt), realer Sensor 4000 W, Batterie 0 | Aktion `shaving`, Sollwert 3000, zurückgegebener Sensorwert 4000 |
| K2 | Regelsensor 4000 W, realer Sensor 1000 W | kein `shaving` (heute würde fälschlich gekappt) |
| K3 | realer Sensor 2000 W, Batterie entlädt 500 W | Hauslast 2500, Aktion `conserving`, Sollwert = Regelsensorwert |
| K4 | realer Sensor `None` (Fallback) | Ergebnis identisch zum Aufruf ohne `physical_actual` |
| K5 | dynamisches Limit 2500 aus Entität, realer Sensor 2800 | `shaving`, Sollwert 2500, Status `peak_limit=2500` |
| K6 | ICP-Clamp: Vertragsleistung 5000, realer Sensor 4800, Regelsensor 0, Ladewunsch 1000 bei Batterie 0 | Ladeleistung auf 200 begrenzt |
| K7 | `peak_review_pending`: Regelsensor ohne neuen Wert, realer Wert neu und über Limit | `True`, sonst `False` (4 Kombinationen) |
| K8 | B6 `capacity_protection_always`, SOC über Schwelle, Regel-Fehler −100, real 3400, Limit 3000 | wirksamer Fehler +400 |
| K9 | B6 aus | wirksamer Fehler −100 |

## 4.5 Konfiguration

| # | Test | Erwartung |
|---|---|---|
| C1 | jede neue Number-Definition | in `CONFIG_NUMBER_DEFINITIONS` vorhanden, Bereich und Default wie in 03 |
| C2 | Controller-Stub ohne neue Schlüssel in `config_entry.data` | Guard `disabled`, `has_physical=False` |
| C3 | neue Schlüssel | in `strings.json`, `en.json`, `de.json` vorhanden (vorhandene Übersetzungstests als Muster suchen) |

## 4.6 Regression und Feldtest

- Nach jedem Meilenstein die komplette Suite (`python -m pytest -q`). Vor AP 1 einmal als Basislinie laufen lassen und das Ergebnis in `docs/fork/baseline-tests.txt` ablegen, damit vorher bestehende Fehler nicht dem Fork zugerechnet werden.
- Feldtest nach M1: 3 Tage laufen lassen, Statuswechsel des Wechselrichters in der HA-Historie zählen. Referenz: 242 Wechsel in 3 Tagen. Ziel: unter 40. Zusätzlich `switches_today` des Diagnosesensors gegen die Historie prüfen.
- Feldtest nach M2: Peak-Fall gezielt erzeugen (SOC unter Schwelle oder Schwelle vorübergehend hochsetzen, großen Verbraucher einschalten) und prüfen, dass der reale Bezug auf das Limit geregelt wird.
