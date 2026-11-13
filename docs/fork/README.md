# Omnibattery-Fork: Konzept

Basis: `ffunes/Omnibattery` Tag `v1.6.0` (= `upstream/main`, Stand 13.11.2026), Branch `fork/main`.
Alle Zeilenangaben beziehen sich auf v1.6.0. Bei der Umsetzung immer über den Funktionsnamen suchen, nicht über die Zeilennummer.

| Datei | Inhalt |
|---|---|
| [01-ist-analyse.md](01-ist-analyse.md) | Regelpipeline, Ursache des Klapperns, Datenfluss des Netzsensors, Korrekturen an den Annahmen |
| [02-architektur.md](02-architektur.md) | Module `relay_guard` und `grid_view`, Hook-Punkte, Zustandsmaschine, Prioritäten, Verschlankung der Oberfläche |
| [03-konfiguration.md](03-konfiguration.md) | Neue Optionen, Defaults, Wertebereiche, Migration |
| [04-teststrategie.md](04-teststrategie.md) | Testszenarien und erwartetes Verhalten |
| [05-arbeitspakete.md](05-arbeitspakete.md) | Arbeitspakete, Modellzuteilung, Reihenfolge, Token-Regeln |
| [06-hacs-und-upstream.md](06-hacs-und-upstream.md) | Ersatz der bestehenden Installation über HACS, Rollback, Upstream-Strategie |

## Die wichtigsten Entscheidungen

1. **Domain und Name bleiben `omnibattery`.** Der Fork ersetzt die bestehende Installation. Config-Entry, Entitäten, Historie und `.storage` bleiben unverändert erhalten. Eine Migrationsfunktion ist nicht nötig. Parallelbetrieb scheidet aus, weil zwei Regler über Modbus um dieselbe Batterie streiten würden.
2. **Keine Erhöhung der Config-Entry-Version** (bleibt 16). Neue Schlüssel werden mit `.get(key, default)` gelesen. Das vermeidet Kollisionen mit künftigen Upstream-Migrationen und erlaubt den Rückweg auf Upstream ohne Datenverlust.
3. **Neue Logik in zwei neuen Modulen**, in `__init__.py` nur wenige Hook-Zeilen. Ziel: unter 60 geänderte Zeilen in Upstream-Dateien pro Thema.
4. **Alle neuen Optionen sind per Default aus.** Ohne Konfiguration verhält sich der Fork exakt wie v1.6.0.

## Entscheidungen vom 13.11.2026 (überschreiben ältere Aussagen in 05 und 06)

- **Ein Durchgang statt zwei Meilensteine.** Thema 1 (Klappern) und Thema 2 (zweiter Netzsensor) werden zusammen umgesetzt und in einer Version ausgeliefert. Beide sind einzeln abschaltbar: der Relais-Schutz über seinen Schalter, der zweite Sensor durch Leerlassen, Peak Shaving über den vorhandenen Schalter.
- **Ablage auf GitHub:** im Repo `SolarPower2024/evcc-addon` unter `custom_components/omnibattery`, neben `custom_components/evcc_peak_fronius`. Folgen für HACS siehe Abschlussbericht der Umsetzung.
- **Realer Netzsensor:** frei wählbar im Options-Flow, keine feste Annahme im Code.
- **Tote Zone:** Die Min-Ladeleistung stellst du selbst auf 30 W. `pd_engage_margin` bleibt 0.
- **Panel:** bleibt. Die Karten der Übersicht außer dem Systemstatus werden über den Schalter `panel_minimal_overview` ausgeblendet.
- **Python 3.14** ist lokal installiert, Test-Umgebung liegt in `.venv-test/`.
- **B6** (Peak-Deckel oberhalb der SOC-Schwelle) bleibt zurückgestellt, bis Peak Shaving mit dem zweiten Sensor im Feld geprüft ist.

## Offene Fragen an dich

Siehe [05-arbeitspakete.md, Abschnitt „Offene Fragen“](05-arbeitspakete.md#offene-fragen).
