# NephroWoche

Wöchentlicher Überblick über neue Studien und Meldungen aus der Nephrologie (Schwerpunkt Dialyse und
Peritonealdialyse), auf Deutsch zusammengefasst und nach Praxisrelevanz sortiert. Eine Web-App für einen Leser,
läuft im Browser am PC und als App-Symbol auf dem iPhone.

- **App:** `index.html` auf GitHub Pages. Sieht nur etwas, wer den persönlichen Link (`?k=…`) hat.
  Beispielausgabe ohne Link: `…/nephrowoche/?demo`
- **Daten:** eigenes Supabase-Projekt (`supabase/setup.sql`). Merken, Gelesen und Hinweise laufen über Funktionen,
  die den Link-Schlüssel prüfen; die Tabellen selbst sind von außen gesperrt.
- **Wochenlauf:** `.github/workflows/weekly.yml`, sonntags ca. 2 Uhr nachts:
  1. sammelt die letzten 7 Tage aus PubMed, Europe PMC (Preprints), ClinicalTrials.gov und den Feeds in `pipeline/feeds.json`,
  2. Claude Haiku bewertet alle Einträge nach Praxisrelevanz (PD und Peritonitis zuerst),
  3. Merkliste und Gelesen-Häkchen verschieben die Gewichte (Interessenprofil, kein Modelltraining),
  4. Claude Opus fasst die 10 bis 15 wichtigsten zusammen (Message Batch, halber Preis), der Rest kommt mit einem Satz in die Liste,
  5. sucht passende deutsche Online-Fortbildungen,
  6. schreibt alles nach Supabase.
- **Montags-Mail:** `.github/workflows/mail.yml`, montags 7 Uhr: drei wichtigste Punkte und Link.

Zusammenfassungen entstehen nur aus Abstracts bzw. Meldungstexten. Jeder Eintrag verlinkt die Quelle; Dosierungen
nennt die App nie selbst, sondern verweist auf Fachinformation und dosing.de.

## Einrichtung (einmalig)

1. **Supabase:** neues Projekt „nephrowoche“ anlegen (Region Frankfurt). Im SQL Editor den Inhalt von
   `supabase/setup.sql` einfügen und ausführen. Die letzte Zeile zeigt den Link-Schlüssel (`token`).
2. **App-Zugang:** in Supabase unter *Project Settings → API* die Project URL und den *publishable/anon key*
   in `config.js` eintragen.
3. **GitHub Pages:** *Settings → Pages → Deploy from a branch → main / (root)*.
   Persönlicher Link: `https://brockmann-director.github.io/nephrowoche/?k=<token>`
4. **Geheimnisse** unter *Settings → Secrets and variables → Actions → New repository secret*:

   | Name | Inhalt |
   |---|---|
   | `ANTHROPIC_API_KEY` | API-Schlüssel aus der Claude Console (später der eigene Schlüssel des Lesers) |
   | `SUPABASE_URL` | Project URL |
   | `SUPABASE_SERVICE_KEY` | *secret/service_role key* (nie in den Code!) |
   | `APP_LINK` | der persönliche Link aus Schritt 3 |
   | `MAIL_TO` | Empfängeradresse |
   | `MAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` | Postfach, über das die Mail verschickt wird (z. B. Strato: `smtp.strato.de`, Port `465`) |
   | `MAIL_BCC` | optional, Kopie an sich selbst |

5. **Test:** *Actions → Wochenlauf → Run workflow*. Mit Häkchen „Nur Quellen testen“ werden nur die Quellen
   abgerufen (ohne Kosten). Ohne Häkchen läuft die echte Ausgabe (meist 20 bis 60 Minuten wegen des Batches).
   Danach *Actions → Montags-Mail → Run workflow* schickt die Mail sofort.

## Kosten

Pro Woche grob 0,50 bis 1,50 $ KI-Kosten (Vorauswahl von ca. 1.000 Abstracts mit Haiku für wenige Cent,
10 bis 15 Zusammenfassungen mit Opus im Batch, eine Websuche für Fortbildungen). Die Zahl steht nach jedem Lauf
im Log und in `nw_issues.stats.cost_usd`. GitHub Actions und Pages sind für öffentliche Repos kostenlos.
Im Repo steht nur Code; Inhalte, Merkliste und Link-Schlüssel liegen in Supabase und in den Geheimnissen.

## Hinweise „Fehlt etwas?“

Landen in der Tabelle `nw_missing` (Supabase → Table Editor).
