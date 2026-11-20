"""KI-Schritte: Vorauswahl (Haiku), Zusammenfassungen (Opus, per Batch zum halben Preis), Fortbildungssuche."""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import re
import time

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

TRIAGE_MODEL = "claude-haiku-5-5"
SUMMARY_MODEL = "claude-opus-5-5"

# Preise je 1 Mio. Tokens (Stand Nov. 2026), nur für die Kostenanzeige im Lauf
PRICES = {TRIAGE_MODEL: (0.10, 0.50), SUMMARY_MODEL: (4.00, 20.00)}

TAGS = ["pd", "peri", "hd", "drug", "tx", "gn", "ckd", "aki", "access", "anemia", "guideline", "congress"]


class Usage:
    def __init__(self):
        self.cost = 0.0
        self.calls = 0

    def add(self, model: str, usage, batch: bool = False):
        if usage is None:
            return
        pin, pout = PRICES.get(model, (5, 25))
        tin = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * 1.25 \
            + (getattr(usage, "cache_read_input_tokens", 0) or 0) * 0.1
        c = tin / 1e6 * pin + (usage.output_tokens or 0) / 1e6 * pout
        self.cost += c * (0.5 if batch else 1)
        self.calls += 1


USAGE = Usage()


def client() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=4, timeout=600)


def _json_text(msg) -> dict:
    text = next((b.text for b in msg.content if b.type == "text"), "")
    return json.loads(text)


# ---------------------------------------------------------------- 1. Vorauswahl

TRIAGE_SYSTEM = """Du sichtest wöchentlich neue nephrologische Literatur und Meldungen für einen niedergelassenen \
Nephrologen in Deutschland, der ein eigenes Dialysezentrum mit Praxis führt (rein klinisch, keine Forschung). \
Schwerpunkte in dieser Reihenfolge: Peritonealdialyse (PD) und besonders die Behandlung der PD-Peritonitis, \
dann Hämodialyse, dann Nephrologie allgemein. Medikamenten-Neuigkeiten (Zulassungen, Warnhinweise, Nebenwirkungen, \
Dosierung bei Niereninsuffizienz) sind ihm wichtig.

Bewerte jeden Eintrag mit score 0 bis 10 nach Praxisrelevanz für ihn:
9-10 ändert voraussichtlich die Behandlung im Dialysezentrum (große RCT, Metaanalyse, neue Leitlinie, Zulassung oder \
Sicherheitswarnung mit Nierenbezug, wichtige PD-/Peritonitis-Daten).
7-8 klar praxisrelevante klinische Studie oder Meldung in seinem Feld.
5-6 interessant, aber kleine/retrospektive Studie oder Randthema.
3-4 eher speziell (Subgruppen, Methodik, Nischen).
0-2 Grundlagenforschung, Tierversuch, Labormethodik, kein Nierenbezug, Fallbericht, Kommentar ohne neue Daten.
PD und PD-Peritonitis eher eine Stufe höher bewerten, Kinder-Nephrologie und reine Transplantationschirurgie eher \
niedriger. Preprints (nicht begutachtet) höchstens 7.

tags: alle passenden aus pd (Peritonealdialyse), peri (Peritonitis bei PD), hd (Hämodialyse/HDF), drug (Medikament \
im Mittelpunkt), tx (Transplantation), gn (Glomerulonephritis/IgA/MN/Lupus/ANCA), ckd (CKD vor Dialyse), aki, access \
(Gefäßzugang/Shunt/Katheter), anemia, guideline (Leitlinie/Konsensus), congress (Kongressbeitrag).
topic: kurzes deutsches Thema für die Trend-Erkennung, möglichst einheitlich benannt, 1-3 Wörter \
(z. B. "IgA-Nephropathie", "PD-Peritonitis", "Hyperkaliämie", "SGLT2-Hemmer", "Gefäßzugang").
title_de: sachlicher deutscher Titel, höchstens 14 Wörter, ohne Studienakronym am Anfang.
oneliner_de: ein deutscher Satz mit dem Kernergebnis und, falls vorhanden, der wichtigsten Zahl aus dem Text. \
Nur Angaben aus dem Text, nichts ergänzen."""

TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "score": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string", "enum": TAGS}},
                    "topic": {"type": "string"},
                    "title_de": {"type": "string"},
                    "oneliner_de": {"type": "string"},
                },
                "required": ["i", "score", "tags", "topic", "title_de", "oneliner_de"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def triage(cands: list[dict], chunk: int = 20, workers: int = 4) -> None:
    """Schreibt score/tags/topic/title_de/oneliner_de direkt in die Kandidaten."""
    c = client()
    chunks = [cands[i:i + chunk] for i in range(0, len(cands), chunk)]

    def run(part: list[dict]):
        lines = []
        for k, x in enumerate(part):
            lines.append(f"### i={k}\nArt: {x['kind']} | Quelle: {x['journal_full']} | Typ: {', '.join(x['pubtypes'][:4])}\n"
                         f"Titel: {x['title']}\nText: {x['abstract'][:1600]}")
        prompt = "Bewerte diese Einträge. Gib für jeden i genau ein Objekt zurück.\n\n" + "\n\n".join(lines)
        for attempt in range(3):
            try:
                msg = c.messages.create(
                    model=TRIAGE_MODEL, max_tokens=16000, system=TRIAGE_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                    output_config={"effort": "low", "format": {"type": "json_schema", "schema": TRIAGE_SCHEMA}},
                )
                USAGE.add(TRIAGE_MODEL, msg.usage)
                if msg.stop_reason == "refusal":
                    return
                for r in _json_text(msg)["items"]:
                    if 0 <= r["i"] < len(part):
                        x = part[r["i"]]
                        x["score"] = max(0, min(10, r["score"]))
                        x["tags"] = sorted(set(r["tags"]))
                        x["topic"] = r["topic"].strip()
                        x["title_de"] = r["title_de"].strip()
                        x["oneliner_de"] = r["oneliner_de"].strip()
                return
            except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError,
                    json.JSONDecodeError, KeyError) as e:
                print(f"  Vorauswahl: Wiederholung nach Fehler: {e}")
                time.sleep(10 * (attempt + 1))

    with cf.ThreadPoolExecutor(workers) as ex:
        list(ex.map(run, chunks))


# ---------------------------------------------------------------- 2. Zusammenfassungen

SUMMARY_SYSTEM = """Du schreibst für einen erfahrenen deutschen Nephrologen (eigenes Dialysezentrum, Schwerpunkt \
Peritonealdialyse) die Zusammenfassung eines Fachartikels oder einer Meldung. Er liest am Montagmorgen in der Praxis.

Regeln:
- Nur Angaben, die im gegebenen Text stehen. Nichts aus eigenem Wissen ergänzen, keine Dosierungen schätzen.
- Zahlen exakt aus dem Text übernehmen (Patientenzahl, Effektgrößen mit 95 %-KI, p-Werte, Nachbeobachtung). \
Deutsche Schreibweise: Dezimalkomma, "95 %-KI".
- summary_de: 4 bis 6 Sätze, ein halber Absatz. Studiendesign und Population, Hauptergebnis mit Zahlen, \
wichtige Nebenergebnisse oder Sicherheit, eine Einschränkung. Klar und nüchtern, Fachsprache ist ok.
- why_de: ein Satz, was das konkret für die Praxis oder das Dialysezentrum heißt. Vorsichtig formulieren, \
wenn die Evidenz schwach ist.
- study_type_de: z. B. "Randomisierte Studie, multizentrisch", "Retrospektive Kohorte, ein Zentrum", "Metaanalyse", \
"Leitlinie", "Zulassung", "Sicherheitsmeldung", "Preprint, Kohorte".
- n_patients: Zahl der Patienten oder Episoden mit Einheit, z. B. "527 Patienten"; "nicht angegeben", wenn sie fehlt.
- drugs: Wirkstoffnamen (INN, deutsch), um die es im Kern geht; leer, wenn keiner.
- Fehlen im Text Zahlen (z. B. Pressemitteilung), das im Text sagen."""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title_de": {"type": "string"},
        "summary_de": {"type": "string"},
        "why_de": {"type": "string"},
        "study_type_de": {"type": "string"},
        "n_patients": {"type": "string"},
        "drugs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title_de", "summary_de", "why_de", "study_type_de", "n_patients", "drugs"],
    "additionalProperties": False,
}


def _summary_params(x: dict) -> dict:
    text = (f"Quelle: {x['journal_full']} ({x['source']}), {x['date']}\nAutoren: {x['authors']}\n"
            f"Publikationstyp: {', '.join(x['pubtypes'][:5])}\nOriginaltitel: {x['title']}\n\nText:\n{x['abstract'][:8000]}")
    return dict(model=SUMMARY_MODEL, max_tokens=8000, system=SUMMARY_SYSTEM,
                messages=[{"role": "user", "content": text}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA}})


def _apply_summary(x: dict, data: dict) -> None:
    x["title_de"] = data["title_de"].strip() or x.get("title_de", "")
    x["summary_de"] = data["summary_de"].strip()
    x["why_de"] = data["why_de"].strip()
    x["study_type_de"] = data["study_type_de"].strip()
    x["n_patients"] = data["n_patients"].strip()
    x["drugs"] = [d.strip() for d in data["drugs"] if d.strip()][:6]


def summarize(top: list[dict], max_wait_h: float = 4.0) -> None:
    """Per Message Batch (halber Preis). Was nach max_wait_h nicht fertig ist, wird direkt nachgeholt."""
    c = client()
    by_id = {f"s{k}": x for k, x in enumerate(top)}
    batch = c.messages.batches.create(requests=[
        Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**_summary_params(x)))
        for cid, x in by_id.items()])
    print(f"  Batch {batch.id} mit {len(by_id)} Anfragen gestartet")
    t0 = time.time()
    while True:
        b = c.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        if time.time() - t0 > max_wait_h * 3600:
            print("  Batch zu langsam, wird abgebrochen; Rest direkt")
            c.messages.batches.cancel(batch.id)
            time.sleep(30)
            break
        time.sleep(60)
    done = set()
    for r in c.messages.batches.results(batch.id):
        if r.result.type == "succeeded" and r.custom_id in by_id:
            msg = r.result.message
            USAGE.add(SUMMARY_MODEL, msg.usage, batch=True)
            if msg.stop_reason in ("refusal", "max_tokens"):
                continue
            try:
                _apply_summary(by_id[r.custom_id], _json_text(msg))
                done.add(r.custom_id)
            except (json.JSONDecodeError, KeyError):
                pass
    for cid, x in by_id.items():
        if cid in done:
            continue
        try:
            msg = c.messages.create(**_summary_params(x))
            USAGE.add(SUMMARY_MODEL, msg.usage)
            if msg.stop_reason not in ("refusal", "max_tokens"):
                _apply_summary(x, _json_text(msg))
        except (anthropic.APIError, json.JSONDecodeError, KeyError) as e:
            print(f"  Zusammenfassung fehlgeschlagen für {x['id']}: {e}")


# ---------------------------------------------------------------- 3. Fortbildungen

EVENTS_PROMPT = """Suche deutschsprachige ONLINE-Fortbildungen für Nephrologen (Webinare, Live-Streams, \
Online-Kurse mit festem Termin) in Deutschland, die zwischen {start} und {end} stattfinden. \
Bevorzugt mit CME-Punkten der Ärztekammer. Mögliche Veranstalter: DGfN, KfH, PHV, Ärztekammern, Unikliniken, \
Fachgesellschaften, seriöse Fortbildungsportale. Keine Präsenzveranstaltungen, keine reinen Industrie-Werbeseiten.
Bevorzuge Termine zu diesen Themen der Woche: {topics}. Allgemeine Nephrologie- oder Dialyse-Fortbildungen sind auch ok.

Nimm nur Termine auf, deren Seite du tatsächlich gefunden hast und deren Datum dort steht. Höchstens 8.
Antworte am Ende ausschließlich mit einem JSON-Block in ```json ... ``` dieser Form:
{{"events": [{{"title": "...", "organizer": "...", "start": "YYYY-MM-DDTHH:MM", "end": "YYYY-MM-DDTHH:MM oder leer", \
"cme": "z. B. 2 CME-Punkte oder leer", "matches": "passendes Thema der Woche oder leer", "url": "https://..."}}]}}
Zeiten in deutscher Ortszeit. Wenn du nichts Passendes findest: {{"events": []}}"""


def find_events(topics: list[str], start: dt.date, end: dt.date) -> list[dict]:
    c = client()
    messages = [{"role": "user", "content": EVENTS_PROMPT.format(
        start=start.strftime("%d.%m.%Y"), end=end.strftime("%d.%m.%Y"), topics=", ".join(topics) or "Dialyse")}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 12,
              "user_location": {"type": "approximate", "country": "DE", "timezone": "Europe/Berlin"}}]
    msg = None
    for _ in range(4):  # pause_turn: Server-Werkzeug-Schleife fortsetzen
        msg = c.messages.create(model=SUMMARY_MODEL, max_tokens=16000, messages=messages, tools=tools,
                                output_config={"effort": "medium"})
        USAGE.add(SUMMARY_MODEL, msg.usage)
        if msg.stop_reason != "pause_turn":
            break
        messages = messages[:1] + [{"role": "assistant", "content": msg.content}]
    if msg is None or msg.stop_reason == "refusal":
        return []
    text = "\n".join(b.text for b in msg.content if b.type == "text")
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{\s*\"events\".*\})", text, re.S)
    if not m:
        return []
    try:
        evs = json.loads(m.group(1)).get("events", [])
    except json.JSONDecodeError:
        return []
    out = []
    for e in evs:
        url = (e.get("url") or "").strip()
        if not url.startswith("http") or not e.get("title"):
            continue
        out.append({k: (e.get(k) or "").strip() for k in ("title", "organizer", "start", "end", "cme", "matches")}
                   | {"url": url})
    return out[:8]
