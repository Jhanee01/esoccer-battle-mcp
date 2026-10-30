"""New-version notice: checks the GitHub releases at most every 6 hours (silently skipped on error)."""

import json
import os
import re
import sqlite3
import urllib.request
from datetime import datetime, timedelta, timezone

from . import __version__, api, db

REPO = "Jhanee01/esoccer-battle-mcp"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
CHECK_EVERY = timedelta(hours=6)
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def parse_version(v: str) -> tuple[int, int, int] | None:
    m = _TAG.match(v if v.startswith("v") else "v" + v)
    return tuple(int(x) for x in m.groups()) if m else None


def latest_of(releases: list[dict]) -> dict | None:
    """A legmagasabb vX.Y.Z kiadás (a `data-latest` adat-csomag és a piszkozatok kimaradnak)."""
    tagged = [r for r in releases
              if not r.get("draft") and not r.get("prerelease") and parse_version(r.get("tag_name", ""))]
    return max(tagged, key=lambda r: parse_version(r["tag_name"]), default=None)


def check(conn: sqlite3.Connection, now: datetime | None = None) -> None:
    if os.environ.get("ESOCCER_BATTLE_NO_UPDATE_CHECK") == "1":
        return
    now = now or datetime.now(timezone.utc)
    last = db.get_meta(conn, "last_release_check")
    if last and now - datetime.fromisoformat(last) < CHECK_EVERY:
        return
    db.set_meta(conn, "last_release_check", now.isoformat(timespec="seconds"))
    try:
        req = urllib.request.Request(RELEASES_URL, headers={"User-Agent": api.HEADERS["User-Agent"],
                                                            "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rel = latest_of(json.loads(r.read()))
    except Exception:
        return
    if rel:
        db.set_meta(conn, "latest_release", json.dumps({
            "tag": rel["tag_name"], "url": rel.get("html_url"), "published": rel.get("published_at"),
            "notes": (rel.get("body") or "")[:3000]}))


def status(conn: sqlite3.Connection) -> dict:
    raw = db.get_meta(conn, "latest_release")
    rel = json.loads(raw) if raw else None
    if not rel:
        return {"latest_release": None, "update_available": False}
    newer = (parse_version(rel["tag"]) or (0, 0, 0)) > (parse_version(__version__) or (0, 0, 0))
    out = {"latest_release": rel["tag"], "update_available": newer, "release_url": rel.get("url")}
    if newer:
        out["release_notes"] = rel.get("notes")
        out["how_to_update"] = (f"Change the version in your MCP client config to {rel['tag']} "
                                f"(uvx --from git+https://github.com/{REPO}@{rel['tag']} esoccer-battle-mcp) "
                                "and restart the client. Your local data is kept.")
    return out
