"""Quellen für den Wochenlauf. Jede Funktion liefert eine Liste einheitlicher Kandidaten.

Kandidat = dict(id, kind, title, abstract, journal, authors, date, url, doi, pmid, pmcid,
                pubtypes, source, oa)
kind: study | preprint | trial | news | drug
"""
from __future__ import annotations

import datetime as dt
import email.utils
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = "NephroWoche/1.0 (weekly literature digest; non-commercial)"


def http_get(url: str, data: bytes | None = None, timeout: int = 60, tries: int = 3) -> bytes:
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 – Quellen sind unzuverlässig, wir versuchen es erneut
            last = e
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f"{url[:90]}: {last}")


def clean(s: str | None) -> str:
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------- PubMed

PUBMED_TERM = (
    '("dialysis"[tiab] OR "hemodialysis"[tiab] OR "haemodialysis"[tiab] OR "hemodiafiltration"[tiab] '
    'OR "peritoneal dialysis"[tiab] OR "peritonitis"[tiab] OR "kidney failure"[tiab] OR "end-stage kidney"[tiab] '
    'OR "end-stage renal"[tiab] OR "chronic kidney disease"[tiab] OR "CKD"[tiab] OR "nephrology"[tiab] '
    'OR "glomerulonephritis"[tiab] OR "IgA nephropathy"[tiab] OR "membranous nephropathy"[tiab] '
    'OR "lupus nephritis"[tiab] OR "FSGS"[tiab] OR "nephrotic"[tiab] OR "ANCA"[tiab] OR "kidney transplant*"[tiab] '
    'OR "renal transplant*"[tiab] OR "acute kidney injury"[tiab] OR "arteriovenous fistula"[tiab] '
    'OR "vascular access"[tiab] OR "hyperkalemia"[tiab] OR "hyperkalaemia"[tiab] OR "renal anemia"[tiab] '
    'OR "SGLT2"[tiab] OR "finerenone"[tiab] OR "eGFR"[tiab] OR "polycystic kidney"[tiab] '
    'OR "Kidney Diseases"[majr] OR "Renal Dialysis"[majr]) '
    'AND hasabstract AND english[la] NOT ("animals"[mh] NOT "humans"[mh])'
)


def pubmed(start: dt.date, end: dt.date, retmax: int = 2500) -> list[dict]:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    q = urllib.parse.urlencode({
        "db": "pubmed", "term": PUBMED_TERM, "datetype": "edat",
        "mindate": start.strftime("%Y/%m/%d"), "maxdate": end.strftime("%Y/%m/%d"),
        "retmax": retmax, "retmode": "json", "tool": "nephrowoche",
    })
    ids = json.loads(http_get(base + "esearch.fcgi?" + q))["esearchresult"]["idlist"]
    out: list[dict] = []
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        body = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(chunk), "retmode": "xml",
                                       "tool": "nephrowoche"}).encode()
        root = ET.fromstring(http_get(base + "efetch.fcgi", data=body, timeout=120))
        for art in root.findall("PubmedArticle"):
            c = _pubmed_article(art)
            if c:
                out.append(c)
        time.sleep(0.4)  # ohne API-Schlüssel max. 3 Anfragen/Sekunde
    return out


def _text(el) -> str:
    return clean("".join(el.itertext())) if el is not None else ""


def _pubmed_article(art) -> dict | None:
    mc = art.find("MedlineCitation")
    a = mc.find("Article")
    pmid = mc.findtext("PMID")
    title = _text(a.find("ArticleTitle"))
    parts = []
    for ab in a.findall("Abstract/AbstractText"):
        label = ab.get("Label")
        t = _text(ab)
        parts.append(f"{label}: {t}" if label else t)
    abstract = "\n".join(p for p in parts if p)
    if not title or not abstract:
        return None
    journal = a.findtext("Journal/ISOAbbreviation") or a.findtext("Journal/Title") or ""
    jfull = a.findtext("Journal/Title") or journal
    authors = []
    for au in a.findall("AuthorList/Author")[:3]:
        ln = au.findtext("LastName")
        ini = au.findtext("Initials") or ""
        if ln:
            authors.append(f"{ln} {ini}".strip())
        elif au.findtext("CollectiveName"):
            authors.append(au.findtext("CollectiveName"))
    n_auth = len(a.findall("AuthorList/Author"))
    doi = ""
    for el in a.findall("ELocationID"):
        if el.get("EIdType") == "doi":
            doi = (el.text or "").strip()
    pmcid = ""
    for el in art.findall("PubmedData/ArticleIdList/ArticleId"):
        if el.get("IdType") == "doi" and not doi:
            doi = (el.text or "").strip()
        if el.get("IdType") == "pmc":
            pmcid = (el.text or "").strip()
    y = a.findtext("Journal/JournalIssue/PubDate/Year") or a.findtext("ArticleDate/Year") or ""
    m = a.findtext("ArticleDate/Month") or ""
    d = a.findtext("ArticleDate/Day") or ""
    date = f"{y}-{m.zfill(2)}-{d.zfill(2)}" if (y and m and d) else y
    pubtypes = [pt.text or "" for pt in a.findall("PublicationTypeList/PublicationType")]
    return {
        "id": f"pmid:{pmid}", "kind": "study", "title": title, "abstract": abstract,
        "journal": journal, "journal_full": jfull,
        "authors": ", ".join(authors) + (" et al." if n_auth > 3 else ""),
        "date": date, "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "doi": doi, "pmid": pmid,
        "pmcid": pmcid, "pubtypes": pubtypes, "source": "PubMed", "oa": bool(pmcid),
    }


# ---------------------------------------------------------------- Europe PMC (Preprints, v. a. medRxiv)

def preprints(start: dt.date, end: dt.date) -> list[dict]:
    query = (f'(SRC:PPR) AND (FIRST_PDATE:[{start.isoformat()} TO {end.isoformat()}]) AND '
             '(TITLE_ABS:dialysis OR TITLE_ABS:"kidney failure" OR TITLE_ABS:"chronic kidney disease" OR '
             'TITLE_ABS:nephropathy OR TITLE_ABS:glomerulonephritis OR TITLE_ABS:"kidney transplant" OR '
             'TITLE_ABS:"acute kidney injury" OR TITLE_ABS:nephrology OR TITLE_ABS:"peritoneal dialysis")')
    out = []
    cursor = "*"
    for _ in range(5):
        q = urllib.parse.urlencode({"query": query, "format": "json", "resultType": "core",
                                    "pageSize": 200, "cursorMark": cursor})
        res = json.loads(http_get("https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + q))
        for r in res.get("resultList", {}).get("result", []):
            title = clean(r.get("title"))
            abstract = clean(r.get("abstractText"))
            if not title or not abstract:
                continue
            doi = r.get("doi", "")
            out.append({
                "id": f"doi:{doi.lower()}" if doi else f"epmc:{r.get('id')}", "kind": "preprint",
                "title": title, "abstract": abstract,
                "journal": (r.get("bookOrReportDetails") or {}).get("publisher", "") or "Preprint",
                "journal_full": "Preprint (noch nicht begutachtet)",
                "authors": _short_authors(r.get("authorString", "")), "date": r.get("firstPublicationDate", ""),
                "url": f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/PPR/{r.get('id')}",
                "doi": doi, "pmid": "", "pmcid": "", "pubtypes": ["Preprint"], "source": "Europe PMC", "oa": True,
            })
        nxt = res.get("nextCursorMark")
        if not nxt or nxt == cursor:
            break
        cursor = nxt
    return out


def _short_authors(s: str) -> str:
    names = [n.strip() for n in s.rstrip(".").split(",") if n.strip()]
    return ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")


# ---------------------------------------------------------------- ClinicalTrials.gov (neu veröffentlichte Ergebnisse)

def trials(start: dt.date, end: dt.date) -> list[dict]:
    q = urllib.parse.urlencode({
        "query.cond": "dialysis OR peritoneal dialysis OR kidney failure OR chronic kidney disease OR "
                      "glomerulonephritis OR IgA nephropathy OR kidney transplantation",
        "filter.advanced": f"AREA[ResultsFirstPostDate]RANGE[{start.isoformat()},{end.isoformat()}]",
        "pageSize": 100, "format": "json",
    })
    res = json.loads(http_get("https://clinicaltrials.gov/api/v2/studies?" + q))
    out = []
    for s in res.get("studies", []):
        p = s.get("protocolSection", {})
        ident = p.get("identificationModule", {})
        nct = ident.get("nctId")
        desc = p.get("descriptionModule", {})
        design = p.get("designModule", {})
        sponsor = p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", "")
        n = design.get("enrollmentInfo", {}).get("count")
        phases = ", ".join(design.get("phases", []) or [])
        abstract = clean(desc.get("briefSummary"))
        if not nct or not abstract:
            continue
        outcomes = [o.get("measure", "") for o in p.get("outcomesModule", {}).get("primaryOutcomes", [])][:3]
        abstract += ("\nPrimary outcomes: " + "; ".join(outcomes)) if outcomes else ""
        abstract += f"\nEnrollment: {n}" if n else ""
        abstract += f"\nPhase: {phases}" if phases else ""
        abstract += "\nResults were newly posted on ClinicalTrials.gov this week."
        out.append({
            "id": f"nct:{nct}", "kind": "trial", "title": clean(ident.get("officialTitle") or ident.get("briefTitle")),
            "abstract": abstract, "journal": "ClinicalTrials.gov", "journal_full": "ClinicalTrials.gov (Studienergebnisse)",
            "authors": sponsor, "date": end.isoformat(), "url": f"https://clinicaltrials.gov/study/{nct}?tab=results",
            "doi": "", "pmid": "", "pmcid": "", "pubtypes": ["Registered trial results"], "source": "ClinicalTrials.gov",
            "oa": True,
        })
    return out


# ---------------------------------------------------------------- RSS / Atom (Fachnachrichten, Behörden, Journale)

KIDNEY_WORDS = re.compile(
    r"kidney|renal|nephr|dialys|glomerul|ckd|egfr|transplant|hyperkal|potassium|uremi|uraemi|"
    r"peritoni|anemia|anaemia|erythropoie|sglt|finerenone|iga|lupus|anca|niere|dialyse|nephro",
    re.I)


def rss(feed: dict, start: dt.date, end: dt.date) -> list[dict]:
    raw = http_get(feed["url"], timeout=45)
    root = ET.fromstring(raw)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = root.findall(".//item") or root.findall(".//a:entry", ns)
    out = []
    for e in entries:
        title = clean(e.findtext("title") or e.findtext("a:title", namespaces=ns))
        link = (e.findtext("link") or "").strip()
        if not link:
            le = e.find("a:link", ns)
            link = le.get("href") if le is not None else ""
        desc = clean(e.findtext("description") or e.findtext("a:summary", namespaces=ns)
                     or e.findtext("a:content", namespaces=ns)
                     or e.findtext("{http://purl.org/rss/1.0/modules/content/}encoded"))
        date = _parse_date(e.findtext("pubDate") or e.findtext("{http://purl.org/dc/elements/1.1/}date")
                           or e.findtext("a:updated", namespaces=ns) or e.findtext("a:published", namespaces=ns))
        if date and not (start <= date <= end):
            continue
        if not title or not link:
            continue
        if feed.get("filter", True) and not KIDNEY_WORDS.search(title + " " + desc):
            continue
        out.append({
            "id": "url:" + re.sub(r"[?#].*$", "", link), "kind": feed.get("kind", "news"), "title": title,
            "abstract": desc[:3000], "journal": feed["name"], "journal_full": feed["name"], "authors": "",
            "date": date.isoformat() if date else "", "url": link, "doi": "", "pmid": "", "pmcid": "",
            "pubtypes": [feed.get("kind", "news")], "source": feed["name"], "oa": True,
        })
    return out


def _parse_date(s: str | None) -> dt.date | None:
    if not s:
        return None
    s = s.strip()
    try:
        return email.utils.parsedate_to_datetime(s).date()
    except Exception:  # noqa: BLE001
        pass
    try:
        return dt.date.fromisoformat(s[:10])
    except Exception:  # noqa: BLE001
        return None
