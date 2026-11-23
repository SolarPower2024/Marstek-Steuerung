# 3. Konfigurationskonzept

## 3.1 Thema 1: `number`-Entitäten (Einträge in `CONFIG_NUMBER_DEFINITIONS`)

| Schlüssel | Name (de) | Bereich | Schritt | Default | Empfehlung für dich |
|---|---|---|---|---|---|
| `pd_relay_cooldown` (vorhanden) | Relais-Mindestlaufzeit | 0–60 s → **0–600 s** | 1 | 0 | 60 s |
| `pd_relay_min_off` | Relais-Mindest-Aus-Zeit | 0–600 s | 5 | 0 | 60 s |
| `pd_direction_lock` | Sperrzeit Richtungswechsel | 0–900 s | 5 | 0 | 90 s |
| `pd_engage_margin` | Einschaltabstand zur Mindestleistung | 0–500 W | 5 | 0 | 0 W |
| `pd_guard_bypass_error` | Sperren aufheben ab Abweichung | 50–3000 W | 10 | 200 | 200 W |
| `pd_relay_switch_budget` | Schaltbudget pro Stunde | 0–120 | 1 | 0 | 30 |

Hinweise:

- `switch`-Entität `pd_relay_guard_enabled` (Default aus). Erst mit „ein“ gelten Einschalthysterese und Sperren. So bleibt das Verhalten nach dem Update exakt wie v1.6.0, bis du den Guard einschaltest.
- Bei eingeschaltetem Guard ist jede Sperre einzeln mit dem Wert 0 abschaltbar. Die Einschalthysterese gilt dann immer.
- Die Erweiterung des Maximums von `pd_relay_cooldown` auf 600 s ist die einzige Änderung an einer bestehenden Definition.
- `pd_engage_margin = 0` bedeutet: Einschalten ab Bedarf ≥ Mindestleistung (statt Mindestleistung − Totband).

## 3.2 Thema 2: Options-Flow und Entitäten

Im Options-Flow, im selben Schritt wie der bisherige Netzsensor (`config_flow.py` ~4136–4184):

| Schlüssel | Typ | Default | Bedeutung |
|---|---|---|---|
| `physical_grid_sensor` | Entität (`sensor`, Leistung), optional | leer | realer Netzbezug am Abrechnungspunkt |
| `physical_grid_inverted` | bool | `false` | Vorzeichen umkehren (positiv = Einspeisung) |
| `capacity_protection_limit_entity` | Entität (`sensor`/`number`/`input_number`), optional | leer | dynamisches Peak-Limit in W |

Als `number`-Entität:

| Schlüssel | Name (de) | Bereich | Default | Empfehlung |
|---|---|---|---|---|
| `capacity_protection_limit_margin` | Sicherheitsabstand Peak-Limit | 0–30 % | 0 | 10 % |
| `capacity_protection_soc_hysteresis` | Entladehysterese Kapazitätsschutz | 0–10 % | 2 | 2 % (ab 1.6.0.5): Nach dem Fall unter die SOC-Schwelle wird erst ab Schwelle + Hysterese wieder normal entladen, z. B. 40 % + 2 % = 42 %. 0 = altes Verhalten |

Als `switch`-Entität (erst mit AP B6):

| Schlüssel | Name (de) | Default |
|---|---|---|
| `capacity_protection_always` | Peak-Limit unabhängig vom SOC einhalten | aus |

Der bisherige Zahlenwert `capacity_protection_limit` bleibt bestehen und ist der Fallback, wenn keine Entität gesetzt oder sie nicht lesbar ist.

Die Erstkonfiguration (`config_flow.py` ~1604–1632) wird nicht erweitert. Der zweite Sensor ist eine Option für bestehende Installationen und spart dort Diff.

## 3.3 Migration

- **Keine Versionserhöhung.** `VERSION` bleibt 16. Alle neuen Schlüssel werden mit `config_entry.data.get(KEY, DEFAULT)` gelesen. Ein fehlender Schlüssel ergibt das alte Verhalten.
- **Update von Upstream v1.6.0 auf den Fork:** keine Aktion nötig, kein Datenumbau.
- **Rückweg auf Upstream:** Upstream ignoriert die unbekannten Schlüssel. Die neuen Entitäten erscheinen als „nicht mehr bereitgestellt“ und können gelöscht werden.
- **Künftige Upstream-Migration (v17 und höher):** läuft unverändert, weil der Fork die Versionsnummer nicht belegt.
- Vorhandenes Muster für Tests: `tests/test_hysteresis_migration.py` zeigt, wie Upstream Defaults prüft. Für den Fork genügt ein Test „fehlende Schlüssel → Guard aus, kein zweiter Sensor“.

## 3.4 Übersetzungen

Pflicht: `strings.json`, `translations/en.json`, `translations/de.json`. Für `es`, `fr`, `nl`, `ca` wird der englische Text eingetragen, damit `hassfest` und vorhandene Übersetzungstests nicht fehlschlagen. Für einen Upstream-PR übersetzt Upstream diese Sprachen selbst nach.
