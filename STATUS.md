# NephroWoche – Stand 22.11.2026

Wöchentlicher Nephrologie-Überblick für Jans Vater, einen Nephrologen mit eigenem Dialysezentrum. Der Schwerpunkt liegt auf Dialyse, vor allem Peritonealdialyse und Peritonitis. Weltweite Studien und Meldungen werden auf Deutsch zusammengefasst und nach Wichtigkeit sortiert. Einträge lassen sich merken und als gelesen markieren.

Das Brainstorm-Dokument mit den Entscheidungen von Anfang an liegt hier: https://claude.ai/artifact/3GM61E6umnWfnvfAqxY47b

## Läuft

- **Version 1.2** der App ist live auf GitHub Pages: https://brockmann-director.github.io/nephrowoche/
  - Der persönliche Link hat die Form `…/?k=<token>`.
  - Mit `?demo` öffnet sich eine Beispielausgabe.
- **Wochenlauf** (`.github/workflows/weekly.yml`): sonntags 01:00 UTC.
  - Der erste echte Lauf am 20.11. dauerte 7 Minuten und kostete etwa 0,78 $. Er brachte 15 ausführliche Einträge, 40 Listeneinträge und 2 Termine.
  - Der Lauf vom 22.11. war erfolgreich.
- **Montags-Mail** (`.github/workflows/mail.yml`): montags um 7 Uhr Berliner Zeit an den Vater.
  - Ein manueller Start mit `force` verschickt sofort.
  - Pro Ausgabe geht höchstens eine Mail raus (`stats.mailed_at`).
  - Die Test-Mail vom 20.11. ist angekommen.

## Aufbau

| Teil | Was |
|---|---|
| `index.html`, `config.js`, `demo.js`, `manifest.webmanifest`, Icons | Die App für Browser und iPhone-Homebildschirm (PWA). Am großen Bildschirm laufen Liste und Eintrag in zwei getrennt scrollenden Spalten. |
| `supabase/setup.sql` | Tabellen `nw_*` und RPCs, die den Token prüfen. RLS ist dicht, Lesen und Schreiben gehen nur über diese RPCs. `service_role` bekommt eigene Rechte, weil im Projekt die automatische Rechtevergabe aus ist. |
| `pipeline/sources.py`, `feeds.json` | Quellen: PubMed (7 Tage), Europe PMC Preprints, ClinicalTrials.gov und RSS-Feeds von Healio, KI, AJKD, PDI, NEJM, Lancet, JAMA, BMJ, EMA und Ärzteblatt. |
| `pipeline/llm.py` | Vorsortierung mit Haiku 5.5, Zusammenfassungen mit Opus 5.5 über die Batch-API und Terminsuche per Websuche. |
| `pipeline/weekly.py` | Ablauf: sammeln, Dubletten entfernen, vorsortieren, nach dem Interessenprofil gewichten (Gemerktes und Gelesenes zählen), Top 10–15 auswählen, zusammenfassen und in Supabase schreiben. Ein wiederholter Lauf derselben Woche ersetzt die Ausgabe. |
| `pipeline/mail.py` | Die Mail mit den drei wichtigsten Punkten und einem Link zur App. Versand über SMTP bei Strato. |

Supabase-Projekt: `dnqkvqixglstkgxayxoq`.

Repo-Secrets:
- `ANTHROPIC_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `APP_LINK`
- `MAIL_TO`
- `SMTP_USER`
- `SMTP_PASS`

Die Werte der Secrets und die Mail-Adresse des Vaters gehören nie ins Repo, denn das Repo ist öffentlich.

## Hinweise

- iPhone: Zuerst den Link aus der Mail in Safari öffnen und erst danach „Zum Home-Bildschirm“ wählen, sonst fehlt dem Symbol der Schlüssel. Als Rückfall hat der Startbildschirm ein Feld, in das man den Link einfügen kann.
- Der Token des Vaters steht in Supabase in der Tabelle `nw_readers`.
- Die Inhalte werden in den nächsten Wochen mit seinen Rückmeldungen nachgeschärft.

## Offen / Ideen

- [ ] Prüfen, ob `ANTHROPIC_API_KEY` auf den Schlüssel des Vaters umgestellt ist. Das war für den 21.11. geplant.
- [ ] Inhalte nach dem Feedback des Vaters anpassen, zum Beispiel die Gewichtung, die Quellen und die Länge.
- [ ] Einen Feed zur Arzneimittelsicherheit finden (FDA, BfArM). Die bisherigen Adressen waren tot.
- [ ] Ein Datumsfilter für den PDI-Feed. Er liefert bisher 0 Treffer.
- [ ] Eigene PDFs an Einträge hängen.
- [ ] Die Zeitschriftenliste, die er über die DGfN-Mitgliedschaft bekommt, als Quellen ergänzen.
