"""Supabase-Zugriff des Wochenlaufs über die REST-Schnittstelle mit dem geheimen Schlüssel (umgeht RLS)."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


class DB:
    def __init__(self):
        self.url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/"
        key = os.environ["SUPABASE_SERVICE_KEY"].strip()
        self.headers = {"apikey": key, "Content-Type": "application/json"}
        if key.startswith("eyJ"):  # alter JWT-Schlüssel; neue sb_secret_-Schlüssel nur im apikey-Header
            self.headers["Authorization"] = "Bearer " + key

    def _req(self, method: str, path: str, body=None, prefer: str | None = None):
        h = dict(self.headers)
        if prefer:
            h["Prefer"] = prefer
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Supabase {method} {path[:60]}: {e.code} {e.read()[:400]!r}") from None

    def select(self, table: str, query: str):
        return self._req("GET", f"{table}?{query}")

    def upsert(self, table: str, rows, on_conflict: str | None = None):
        if not rows:
            return
        path = table + (f"?on_conflict={on_conflict}" if on_conflict else "")
        for i in range(0, len(rows), 100):
            self._req("POST", path, rows[i:i + 100], prefer="resolution=merge-duplicates,return=minimal")

    def delete(self, table: str, query: str):
        self._req("DELETE", f"{table}?{query}", prefer="return=minimal")

    def update(self, table: str, query: str, values: dict):
        self._req("PATCH", f"{table}?{query}", values, prefer="return=minimal")

    # ------------------------------------------------------------ Abfragen für den Lauf

    def recent_item_ids(self, since_iso: str) -> set[str]:
        rows = self.select("nw_items", "select=id&created_at=gte." + urllib.parse.quote(since_iso))
        return {r["id"] for r in rows or []}

    def recent_topics(self, issues: int = 8) -> list[dict]:
        iss = self.select("nw_issues", f"select=id&published_at=not.is.null&order=published_at.desc&limit={issues}") or []
        if not iss:
            return []
        ids = ",".join(urllib.parse.quote(i["id"]) for i in iss)
        return self.select("nw_items", f"select=issue_id,topic&issue_id=in.({ids})") or []

    def interest(self) -> list[dict]:
        """Gemerkt/gelesen mit Themen und Tags der Einträge, für das Interessenprofil."""
        return self.select("nw_state", "select=starred,read,nw_items(topic,tags)&limit=2000") or []
