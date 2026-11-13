# 6. HACS-Ersatz und Upstream-Strategie

## 6.1 Wie HACS zuordnet

HACS merkt sich pro Installation das **GitHub-Repository** (bei dir `ffunes/Omnibattery`, Repository-ID 1283108475, Standard-Repo) und den Zielordner `custom_components/omnibattery`. Home Assistant selbst kennt nur die **Domain** `omnibattery`. Config-Entry, Geräte, Entitäten, Historie und `.storage` hängen an Domain, Entry-ID und `unique_id`, nicht an HACS.

Folge: Bleibt die Domain gleich, tauscht man nur die Dateien und die HACS-Zuordnung. Home Assistant merkt vom Wechsel nichts außer der neuen Version.

Einfach die Dateien von Hand überschreiben funktioniert technisch auch, ist aber die schlechtere Wahl: HACS glaubt dann weiter, Upstream v1.6.0 sei installiert, und das nächste Upstream-Update überschreibt den Fork.

## 6.2 Voraussetzungen im Fork-Repository

| Datei | Änderung |
|---|---|
| `hacs.json` | `"name": "Omnibattery (Fork)"`, damit er in HACS unterscheidbar ist |
| `manifest.json` | `"version": "1.6.0.1"` (vierte Stelle = Fork-Zähler), `documentation`, `issue_tracker`, `codeowners` auf den Fork. `domain` und `name` bleiben. |
| `.github/workflows/` | `claude-discussions.yml`, `claude-issues.yml`, `feature-request-comment.yml`, `docs.yml` im Fork löschen oder deaktivieren (brauchen Secrets von Upstream). `tests.yml`, `hacs.yml`, `hassfest.yml` behalten. |
| GitHub-Release | Tag `v1.6.0.1` als Release veröffentlichen. HACS bietet nur Releases als Versionen an. |

Das Repository muss für HACS erreichbar sein. Öffentlich ist der sichere Weg. Lizenz von Upstream prüfen und beibehalten (`LICENSE` liegt im Repo).

## 6.3 Umstieg, Schritt für Schritt

1. **Sicherung:** vollständiges HA-Backup. Zusätzlich die Omnibattery-Konfigurationssicherung (Funktion der Integration).
2. HACS → Omnibattery → Menü ⋮ → **Entfernen**. Das löscht nur den Ordner `custom_components/omnibattery`. Den Hinweis, die Integration vorher zu löschen, **nicht** befolgen: Der Eintrag unter Einstellungen → Geräte & Dienste bleibt stehen. **Home Assistant jetzt nicht neu starten.**
3. HACS → Menü ⋮ → **Benutzerdefinierte Repositories** → URL des Forks, Typ „Integration“ → hinzufügen.
4. „Omnibattery (Fork)“ öffnen → **Herunterladen**, Version `v1.6.0.1`.
5. Home Assistant neu starten.
6. Prüfen: Integration geladen, Entitäten haben ihre alten IDs, Historie durchgehend, neue Entitäten (Guard-Parameter, Diagnosesensor) vorhanden und Guard aus.
7. Guard-Parameter laut 03 setzen und den Schalter einschalten.

Falls Home Assistant zwischen Schritt 2 und 4 doch neu startet: Die Integration zeigt „nicht geladen“, Daten gehen dabei nicht verloren. Schritte 3–5 nachholen.

Zeitpunkt: abends oder bei leerer PV, weil die Batterie während des Neustarts ungeregelt ist.

## 6.4 Rückweg auf Upstream

Gleiche Schritte in umgekehrter Richtung (Fork in HACS entfernen, `ffunes/Omnibattery` wieder herunterladen, neu starten). Upstream ignoriert die zusätzlichen Konfigurationsschlüssel. Die Fork-Entitäten bleiben als verwaist stehen und können gelöscht werden.

Sobald ein Upstream-Release eine höhere Config-Version als der Fork mitbringt und du es installiert hattest, ist ein Wechsel **zurück auf einen älteren Fork-Stand** nicht mehr möglich (Home Assistant verweigert Downgrades der Config-Version). Der Fork muss dann zuerst auf dieses Upstream-Release rebased werden.

## 6.5 Fork aktuell halten

- `upstream` = `ffunes/Omnibattery`, `origin` = dein Fork. `fork/main` enthält Upstream-Tag plus Fork-Commits.
- Nur auf **stabile Upstream-Tags** rebasen (`v1.6.1`, `v1.7.0`), nicht auf Betas und nicht auf jeden Commit. Ablauf: `git fetch upstream --tags` → `git rebase v1.x.y` → Suite laufen lassen → Release `v1.x.y.1`.
- Commits thematisch sauber halten (ein Commit pro Arbeitspaket). Das macht Konflikte beim Rebase lokal und lesbar.
- Konfliktträchtig sind nur die Hook-Zeilen in `__init__.py` und die Tabelle in `integration_const.py`. Die Hooks sind in 02 dokumentiert und lassen sich im Zweifel neu setzen.
- Jeder Hook bekommt den Kommentar `# FORK:` mit Verweis auf dieses Dokument. `grep -n "FORK:"` listet nach einem Rebase alle Eingriffe.

## 6.6 Was als PR an Upstream geht

| Teil | PR-tauglich | Begründung |
|---|---|---|
| `RelayGuard` mit Min-Aus-Zeit, Richtungssperre, Einschalthysterese | ja, guter Kandidat | Allgemeines Problem jeder Batterie mit Relais. Default aus, eigene Tests, kleiner Hook. Zuerst als Issue mit deinen Messdaten (242 Wechsel in 3 Tagen) ankündigen. |
| Diagnosesensor zum Guard | ja, zusammen mit dem Guard | |
| Schaltbudget | eher eigener, späterer PR | Zusätzliche Komplexität, erst nach Annahme des Kerns. |
| Zweiter Netzsensor für Peak Shaving und Vertragsleistung | ja, aber nur nach Abstimmung | Greift in die Stale-Logik ein (P5). Upstream sollte das Design vorab im Issue sehen. |
| Peak-Limit aus Entität | ja | Klein, allgemein nützlich (dynamische Tarifstufen). |
| Peak-Deckel oberhalb der SOC-Schwelle (B6) | offen | Verändert die Semantik von Peak Shaving. |
| UI-Reduktion (Thema 3) | nein | persönliche Präferenz |
| `hacs.json`, `manifest.json`, Workflows | nein | fork-spezifisch |

Wird ein PR angenommen, entfällt der entsprechende Fork-Commit beim nächsten Rebase. Ziel ist, dass der Fork mit der Zeit kleiner wird.

PR-Branches zweigen von `upstream/main` ab, nicht von `fork/main`, und enthalten nur das jeweilige Thema ohne `# FORK:`-Kommentare.
