"""NephroWoche – Wochenlauf.

Sammelt die Neuerscheinungen der letzten 7 Tage, lässt sie vorsortieren, fasst die wichtigsten zusammen
und schreibt die Ausgabe nach Supabase. Aufruf:

    python pipeline/weekly.py            # voller Lauf (braucht ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY)
    python pipeline/weekly.py --dry-run  # nur Quellen abrufen und zählen, ohne KI und ohne Datenbank
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import pathlib
import re
import sys
import urllib.parse
import zoneinfo

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import sources  # noqa: E402

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "out"

TOP_MIN, TOP_MAX, LIST_MAX = 10, 15, 40
MAX_TRIAGE = 1500

DROP_TYPES = {"Comment", "Editorial", "Erratum", "Published Erratum", "Retraction of Publication",
              "Retracted Publication", "Expression of Concern", "News", "Biography", "Interview", "Letter",
              "Case Reports"}

PRIORITY_JOURNALS = re.compile(
    r"^(N Engl J Med|Lancet|JAMA|BMJ|Nat Med|Ann Intern Med|J Am Soc Nephrol|Clin J Am Soc Nephrol|Kidney Int|"
    r"Nephrol Dial Transplant|Am J Kidney Dis|Perit Dial Int|Kidney Int Rep|Kidney Med|Clin Kidney J|"
    r"Lancet Diabetes Endocrinol|JAMA Intern Med|Circulation|Eur Heart J|Am J Transplant|Transplantation|"
    r"Semin Dial|Hemodial Int|Blood Purif|BMC Nephrol|J Nephrol|Nephrology \(Carlton\)|Kidney360|"
    r"Cochrane Database Syst Rev|Diabetes Care)$", re.I)
PD_WORDS = re.compile(r"peritoneal dialysis|peritonitis|\bPD\b|icodextrin|exit[- ]site|CAPD|APD\b", re.I)

BADGE = {"pd": ("PD", "pd"), "peri": ("Peritonitis", "pd"), "hd": ("HD", ""), "drug": ("Medikament", "drug"),
         "tx": ("Transplantation", ""), "gn": ("GN", ""), "ckd": ("CKD", ""), "aki": ("ANV", ""),
         "access": ("Gefäßzugang", ""), "anemia": ("Anämie", ""), "guideline": ("Leitlinie", "warn"),
         "congress": ("Kongress", "warn")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()

    today = dt.datetime.now(BERLIN).date()
    end = today - dt.timedelta(days=1)
    start = end - dt.timedelta(days=args.days - 1)
    monday = today + dt.timedelta(days=(7 - today.weekday()) % 7 or 7) if today.weekday() != 0 else today
    iso = monday.isocalendar()
    issue_id = f"{iso.year}-W{iso.week:02d}"
    label = f"{start.strftime('%d.%m.')}–{end.strftime('%d.%m.%Y')}"
    print(f"Ausgabe {issue_id} (Lesemontag {monday}), Zeitraum {label}")

    stats: dict = {"sources": {}, "failed": []}

    # ---------------------------------------------------------- 1. Sammeln
    cands: list[dict] = []

    def take(name: str, fn):
        try:
            got = fn()
            stats["sources"][name] = len(got)
            cands.extend(got)
            print(f"  {name}: {len(got)}")
        except Exception as e:  # noqa: BLE001
            stats["failed"].append(f"{name}: {str(e)[:160]}")
            print(f"  {name}: FEHLER {e}")

    take("PubMed", lambda: sources.pubmed(start, end))
    take("Preprints (Europe PMC)", lambda: sources.preprints(start, end))
    take("ClinicalTrials.gov", lambda: sources.trials(start, end))
    feeds = json.loads((ROOT / "pipeline" / "feeds.json").read_text())["feeds"]
    for f in feeds:
        take(f["name"], lambda f=f: sources.rss(f, start, end))

    # ---------------------------------------------------------- 2. Bereinigen
    db = None
    if not args.dry_run:
        from db import DB
        db = DB()
    seen_ids = db.recent_item_ids((today - dt.timedelta(days=60)).isoformat(), except_issue=issue_id) if db else set()

    def norm(t: str) -> str:
        return re.sub(r"[^a-z0-9]", "", t.lower())[:90]

    uniq: dict[str, dict] = {}
    dois: set[str] = set()
    titles: set[str] = set()
    for x in cands:  # PubMed zuerst, also gewinnt der begutachtete Artikel gegen Preprint/RSS
        if set(x["pubtypes"]) & DROP_TYPES and "Randomized Controlled Trial" not in x["pubtypes"]:
            continue
        doi = x["doi"].lower()
        nt = norm(x["title"])
        if x["id"] in seen_ids or x["id"] in uniq or (doi and doi in dois) or nt in titles:
            continue
        if doi and f"doi:{doi}" in seen_ids:
            continue
        uniq[x["id"]] = x
        if doi:
            dois.add(doi)
        titles.add(nt)
    items = list(uniq.values())
    print(f"Nach Bereinigung: {len(items)} von {len(cands)}")

    def prio(x):
        return (bool(PD_WORDS.search(x["title"] + " " + x["abstract"][:800])) * 2
                + bool(PRIORITY_JOURNALS.match(x["journal"])) * 2 + (x["kind"] in ("drug", "news", "trial")))
    items.sort(key=prio, reverse=True)
    items = items[:MAX_TRIAGE]
    stats["candidates"] = len(items)

    OUT.mkdir(exist_ok=True)
    if args.dry_run:
        (OUT / "candidates.json").write_text(json.dumps(items, ensure_ascii=False, indent=1))
        by_kind = collections.Counter(x["kind"] for x in items)
        print("Arten:", dict(by_kind))
        print("PD-Treffer:", sum(1 for x in items if PD_WORDS.search(x["title"] + " " + x["abstract"])))
        for x in items[:15]:
            print(f"  - [{x['journal']}] {x['title'][:110]}")
        print(json.dumps(stats, ensure_ascii=False, indent=1))
        return 0

    # ---------------------------------------------------------- 3. Vorauswahl
    import llm
    print(f"Vorauswahl mit {llm.TRIAGE_MODEL} …")
    llm.triage(items)
    items = [x for x in items if "score" in x]
    print(f"  bewertet: {len(items)}, Kosten bisher ca. {llm.USAGE.cost:.2f} $")

    # Interessenprofil aus Merken/Gelesen (kein Modelltraining: einfache Gewichte je Thema/Tag)
    weights: collections.Counter = collections.Counter()
    for s in db.interest():
        it = s.get("nw_items") or {}
        w = 2.0 if s.get("starred") else (0.5 if s.get("read") else 0)
        if not w:
            continue
        if it.get("topic"):
            weights["topic:" + it["topic"].lower()] += w
        for t in it.get("tags") or []:
            weights["tag:" + t] += w * 0.3

    for x in items:
        boost = min(1.0, weights["topic:" + x["topic"].lower()] / 6
                    + sum(weights["tag:" + t] for t in x["tags"]) / 30)
        s = x["score"] + boost + 0.5 * ("pd" in x["tags"]) + 0.7 * ("peri" in x["tags"])
        if x["kind"] == "preprint":
            s -= 0.5
        x["final"] = round(min(10.0, s), 2)
        x["profile_boost"] = round(boost, 2)

    items.sort(key=lambda x: x["final"], reverse=True)
    top = [x for x in items if x["final"] >= 6.5][:TOP_MAX]
    if len(top) < TOP_MIN:
        top = items[:TOP_MIN]
    top_ids = {x["id"] for x in top}
    lst = [x for x in items if x["id"] not in top_ids and x["final"] >= 4][:LIST_MAX]

    # Trend: Themen dieser Woche gegen die letzten Ausgaben
    week_topics = collections.Counter(x["topic"] for x in items if x["score"] >= 3 and x["topic"])
    hist = db.recent_topics(except_issue=issue_id)
    n_hist = max(1, len({h["issue_id"] for h in hist}))
    hist_c = collections.Counter(h["topic"] for h in hist)
    trend_val = {}
    for t, c in week_topics.items():
        ratio = (c + 1) / (hist_c[t] / n_hist + 1)
        trend_val[t] = round(min(10.0, c * ratio / 2), 1)
    trends = [{"topic": t, "count": week_topics[t]} for t, _ in
              sorted(((t, v) for t, v in trend_val.items() if week_topics[t] >= 3), key=lambda z: -z[1])[:4]]

    # ---------------------------------------------------------- 4. Zusammenfassungen
    print(f"Zusammenfassungen für {len(top)} Einträge mit {llm.SUMMARY_MODEL} (Batch) …")
    llm.summarize(top)

    # ---------------------------------------------------------- 5. Fortbildungen
    events = []
    try:
        topic_list = [t["topic"] for t in trends] + [x["topic"] for x in top[:5]]
        events = llm.find_events(list(dict.fromkeys(topic_list))[:6], monday, monday + dt.timedelta(days=42))
        print(f"Fortbildungen: {len(events)}")
    except Exception as e:  # noqa: BLE001
        stats["failed"].append(f"Fortbildungssuche: {str(e)[:160]}")

    # ---------------------------------------------------------- 6. Schreiben
    stats["cost_usd"] = round(llm.USAGE.cost, 2)
    stats["top"] = len(top)
    stats["list"] = len(lst)
    lead = [{"id": x["id"], "title": x.get("title_de", ""), "text": x.get("oneliner_de", "")} for x in top[:3]]

    rows = [item_row(x, issue_id, "top", k + 1, trend_val) for k, x in enumerate(top)]
    rows += [item_row(x, issue_id, "list", k + 1, trend_val) for k, x in enumerate(lst)]
    (OUT / "issue.json").write_text(json.dumps({"issue": issue_id, "label": label, "lead": lead, "trends": trends,
                                                "stats": stats, "items": rows, "events": events},
                                               ensure_ascii=False, indent=1))

    db.upsert("nw_issues", [{"id": issue_id, "week_label": label, "lead": lead, "trends": trends,
                             "stats": stats, "published_at": None}])
    db.delete("nw_events", f"issue_id=eq.{issue_id}")
    # Wiederholter Lauf für dieselbe Woche: alte Einträge, die nicht mehr dabei sind, entfernen
    stale = db.item_ids_of(issue_id) - {r["id"] for r in rows}
    for sid in stale:
        db.delete("nw_items", "id=eq." + urllib.parse.quote(sid))
    db.upsert("nw_items", rows)
    db.upsert("nw_events", [{"issue_id": issue_id, "title": e["title"], "organizer": e["organizer"],
                             "starts_at": to_ts(e["start"]), "ends_at": to_ts(e["end"]), "cme": e["cme"],
                             "matches": e["matches"], "url": e["url"]} for e in events])
    db.update("nw_issues", f"id=eq.{issue_id}", {"published_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    print(f"Fertig: {len(top)} ausführlich, {len(lst)} in der Liste, {len(events)} Fortbildungen, "
          f"KI-Kosten ca. {stats['cost_usd']} $")
    if stats["failed"]:
        print("Ausgefallene Quellen:", *stats["failed"], sep="\n  ")
    return 0


def to_ts(s: str):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=BERLIN)
    return d.isoformat()


def item_row(x: dict, issue_id: str, tier: str, rank: int, trend_val: dict) -> dict:
    tags = list(x.get("tags", []))
    if x["kind"] == "drug" and "drug" not in tags:
        tags.append("drug")
    badges = [list(BADGE[t]) for t in tags if t in BADGE]
    if x["kind"] == "preprint":
        badges.insert(0, ["Preprint, nicht begutachtet", "warn"])
    if x["kind"] == "trial":
        badges.insert(0, ["Studienregister", ""])
    if x["kind"] in ("news", "drug"):
        badges.insert(0, ["Meldung", ""])
    n_txt = x.get("n_patients", "")
    m = re.match(r"\s*([\d.]+)", n_txt.replace(".", "")) if n_txt else None
    if m and m.group(1).isdigit() and int(m.group(1)) < 50:
        badges.append(["kleine Studie", "warn"])
    facts = []
    if x.get("study_type_de"):
        facts.append(["Studientyp", x["study_type_de"]])
    if n_txt:
        facts.append(["Umfang", n_txt])
    facts.append(["Quelle", x["journal_full"]])
    if x.get("date"):
        facts.append(["Erschienen", x["date"]])
    if x.get("doi"):
        facts.append(["DOI", x["doi"]])
    if x.get("pmid"):
        facts.append(["PMID", x["pmid"]])
    if x.get("pmcid"):
        facts.append(["PMC", x["pmcid"]])
    src = " · ".join(p for p in [x.get("authors", ""), x["journal"], x.get("date", "")] if p)
    full = ""
    if x.get("pmcid"):
        full = f"https://pmc.ncbi.nlm.nih.gov/articles/{x['pmcid']}/"
    elif x.get("doi"):
        full = f"https://doi.org/{x['doi']}"
    return {
        "id": x["id"], "issue_id": issue_id, "tier": tier, "rank": rank, "score": x.get("final", x.get("score", 0)),
        "trend": trend_val.get(x.get("topic", ""), 0), "tags": tags, "topic": x.get("topic", ""), "badges": badges,
        "title_de": x.get("title_de") or x["title"], "title_en": x["title"], "source_line": src,
        "summary_de": x.get("summary_de", "") if tier == "top" else "", "why_de": x.get("why_de", "") if tier == "top" else "",
        "oneliner_de": x.get("oneliner_de", ""), "facts": facts, "drugs": x.get("drugs", []),
        "abstract_en": x["abstract"], "url": x["url"], "fulltext_url": full, "open_access": bool(x.get("oa")),
    }


if __name__ == "__main__":
    sys.exit(main())
