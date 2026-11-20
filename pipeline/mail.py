"""Montags-Erinnerung: die drei wichtigsten Punkte plus Link zur App.

Läuft per GitHub Actions montags zweimal (05:00 und 06:00 UTC) und verschickt nur, wenn es in Berlin
gerade 7 Uhr ist (so stimmt es in Sommer- und Winterzeit). --force verschickt sofort (zum Testen).

Geheimnisse: SUPABASE_URL, SUPABASE_SERVICE_KEY, APP_LINK (persönlicher Link), MAIL_TO, SMTP_USER, SMTP_PASS.
Optional: SMTP_HOST (Standard smtp.strato.de), SMTP_PORT (465), MAIL_FROM (Standard = SMTP_USER), MAIL_BCC.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import pathlib
import smtplib
import ssl
import sys
import zoneinfo
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from db import DB  # noqa: E402

BERLIN = zoneinfo.ZoneInfo("Europe/Berlin")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    now = dt.datetime.now(BERLIN)
    if not args.force and now.hour != 7:
        print(f"In Berlin ist es {now:%H:%M}, die Mail geht nur um 7 Uhr raus.")
        return 0

    db = DB()
    iss = db.select("nw_issues", "select=*&published_at=not.is.null&order=published_at.desc&limit=1")
    if not iss:
        print("Noch keine Ausgabe veröffentlicht.")
        return 0
    issue = iss[0]
    stats = issue.get("stats") or {}
    if stats.get("mailed_at") and not args.force:
        print("Für diese Ausgabe wurde schon gemailt.")
        return 0
    published = dt.datetime.fromisoformat(issue["published_at"])
    if not args.force and now - published > dt.timedelta(days=3):
        print("Keine neue Ausgabe seit dem Wochenende – keine Mail.")
        return 0

    n = db.select("nw_items", f"select=id&issue_id=eq.{issue['id']}&tier=eq.top") or []
    link = os.environ["APP_LINK"].strip()
    lead = issue.get("lead") or []
    trends = issue.get("trends") or []

    text_lines = [f"Guten Morgen,", "", f"die NephroWoche ist da ({issue['week_label']}, {len(n)} ausführliche Einträge).", "",
                  "Das Wichtigste:"]
    for k, x in enumerate(lead, 1):
        text_lines += [f"{k}. {x['title']}", f"   {x['text']}"]
    if trends:
        text_lines += ["", "Im Trend: " + ", ".join(t["topic"] for t in trends)]
    text_lines += ["", f"Zur Ausgabe: {link}", "", "– NephroWoche"]

    esc = html.escape
    li = "".join(f'<li style="margin:0 0 14px"><b>{esc(x["title"])}</b><br>{esc(x["text"])}</li>' for x in lead)
    tr = (f'<p style="margin:16px 0 0;color:#4f5b60">Im Trend: {esc(", ".join(t["topic"] for t in trends))}</p>'
          if trends else "")
    body_html = f"""<!doctype html><html><body style="margin:0;background:#fbfaf6">
<div style="max-width:640px;margin:0 auto;padding:24px 20px;font-family:Verdana,Arial,sans-serif;font-size:18px;line-height:1.55;color:#1d2427">
<h1 style="font-family:Georgia,serif;font-size:28px;margin:0 0 4px">NephroWoche</h1>
<p style="margin:0 0 20px;color:#4f5b60">{esc(issue['week_label'])} · {len(n)} ausführliche Einträge</p>
<h2 style="font-family:Georgia,serif;font-size:21px;margin:0 0 10px">Das Wichtigste der Woche</h2>
<ol style="padding-left:22px;margin:0">{li}</ol>{tr}
<p style="margin:28px 0"><a href="{esc(link)}" style="background:#1f5f74;color:#ffffff;text-decoration:none;font-weight:bold;padding:14px 24px;border-radius:8px;display:inline-block">Ausgabe öffnen</a></p>
<p style="font-size:14px;color:#4f5b60">KI-Zusammenfassungen ersetzen nicht das Original. Jeder Eintrag in der App verlinkt die Quelle.</p>
</div></body></html>"""

    msg = EmailMessage()
    sender = (os.environ.get("MAIL_FROM") or os.environ["SMTP_USER"]).strip()
    msg["From"] = formataddr(("NephroWoche", sender))
    msg["To"] = os.environ["MAIL_TO"].strip()
    if os.environ.get("MAIL_BCC", "").strip():
        msg["Bcc"] = os.environ["MAIL_BCC"].strip()
    msg["Subject"] = f"NephroWoche {issue['week_label']}: " + (lead[0]["title"] if lead else "neue Ausgabe")
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg.set_content("\n".join(text_lines))
    msg.add_alternative(body_html, subtype="html")

    host = (os.environ.get("SMTP_HOST") or "smtp.strato.de").strip()
    port = int(os.environ.get("SMTP_PORT", "465") or 465)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=ctx)
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.send_message(msg)
    stats["mailed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    db.update("nw_issues", f"id=eq.{issue['id']}", {"stats": stats})
    print("Mail verschickt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
