"""Minimal client for the public Esoccer Battle JSON API."""

import json
import threading
import time
import urllib.parse
import urllib.request

BASE = "https://football.esportsbattle.com/api/"
HEADERS = {"User-Agent": "esoccer-battle-mcp (+https://github.com/Jhanee01/esoccer-battle-mcp)",
           "Accept": "application/json"}
PAUSE_S = 0.3        # kíméletes tempó: ennyi szünet két kérés között
RETRIES = 3

_lock = threading.Lock()
_last_request = 0.0


class ApiError(Exception):
    pass


def get(path: str, timeout: float = 30):
    global _last_request
    for attempt in range(RETRIES):
        with _lock:
            wait = PAUSE_S - (time.monotonic() - _last_request)
            if wait > 0:
                time.sleep(wait)
            _last_request = time.monotonic()
        try:
            req = urllib.request.Request(BASE + path, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception as e:
            if attempt == RETRIES - 1:
                raise ApiError(f"{path}: {e}") from e
            time.sleep(2 * (attempt + 1))


def tournaments_on(day: str) -> list[dict]:
    """Az API szerint az adott naptári napon (YYYY-MM-DD) induló tornák, minden oldal."""
    d = day.replace("-", "/")
    out, page = [], 1
    while True:
        q = "tournaments?page=%d&dateFrom=%s&dateTo=%s" % (
            page, urllib.parse.quote(d + " 00:00"), urllib.parse.quote(d + " 23:59"))
        r = get(q)
        out += r.get("tournaments", [])
        if page >= (r.get("totalPages") or 1):
            return out
        page += 1


def matches_of(tournament_id: int) -> list[dict]:
    return get(f"tournaments/{tournament_id}/matches")
