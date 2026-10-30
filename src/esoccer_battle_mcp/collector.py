"""Download Battle days from the API into the local database."""

import sqlite3
from datetime import date, datetime, timedelta, timezone

from . import api
from .db import set_meta, today_battle_day
from .schedule import Schedule, battle_day, to_local

FINISHED_MATCH = 3
FINISHED_TOURNAMENT = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _valid_match(m: dict) -> bool:
    try:
        return (isinstance(m["id"], int) and isinstance(m["date"], str)
                and all(isinstance(m[p]["nickname"], str)
                        and isinstance(m[p]["team"]["token_international"], str)
                        for p in ("participant1", "participant2")))
    except (KeyError, TypeError):
        return False


def _note_unknown_venue(conn: sqlite3.Connection, venue: str, day: str) -> None:
    conn.execute(
        "INSERT INTO unknown_venues(venue, first_seen, last_seen, tournaments) VALUES(?, ?, ?, 1) "
        "ON CONFLICT(venue) DO UPDATE SET last_seen = MAX(last_seen, excluded.last_seen), "
        "first_seen = MIN(first_seen, excluded.first_seen), tournaments = tournaments + 1",
        (venue, day, day))


def store_tournament(conn: sqlite3.Connection, sched: Schedule, t: dict, matches: list[dict]) -> int:
    """Egy torna és befejezett meccseinek mentése. Visszaadja a mentett meccsek számát."""
    venue = t["location"]["token"]
    start = to_local(t["start_date"])
    day = battle_day(t.get("token_international"), start)
    turnus, session = sched.classify(venue, day, start.strftime("%H:%M"))
    conn.execute(
        "INSERT OR REPLACE INTO tournaments(id, name, league, venue, start_utc, start_local,"
        " battle_day, status, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (t["id"], t.get("token_international"), (t.get("league") or {}).get("token_international"),
         venue, t["start_date"], start.strftime("%Y-%m-%d %H:%M"), day, t.get("status_id"), _now()))
    rows = []
    for m in matches:
        if m.get("status_id") != FINISHED_MATCH:
            continue
        p1, p2 = m["participant1"], m["participant2"]
        if p1.get("score") is None or p2.get("score") is None:
            continue
        local = to_local(m["date"])
        rows.append((m["id"], t["id"], day, m["date"], local.strftime("%Y-%m-%d"), local.strftime("%H:%M"),
                     venue, sched.format_of(venue), turnus, session,
                     p1["nickname"], p1["team"]["token_international"],
                     p2["nickname"], p2["team"]["token_international"],
                     p1["score"], p2["score"], p1.get("cornerScore"), p2.get("cornerScore")))
    conn.executemany(
        "INSERT OR REPLACE INTO matches(id, tournament_id, battle_day, start_utc, local_date, local_time,"
        " venue, format, turnus, session, player1, team1, player2, team2, score1, score2,"
        " corners1, corners2) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def fetch_day(conn: sqlite3.Connection, sched: Schedule, day: str) -> dict:
    """Egy Battle-nap (06:00 → másnap 06:00) letöltése. Az API naptári napra szűr, ezért
    a nap és a másnap tornáit is lekéri, és a torna neve szerinti Battle-napot tartja meg."""
    nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    seen, tournaments = set(), []
    for d in (day, nxt):
        for t in api.tournaments_on(d):
            if t["id"] in seen:
                continue
            seen.add(t["id"])
            if battle_day(t.get("token_international"), to_local(t["start_date"])) == day:
                tournaments.append(t)

    stats = {"battle_day": day, "tournaments": 0, "matches": 0, "skipped": 0, "unmatched_blocks": 0}
    all_finished = bool(tournaments)
    for t in tournaments:
        venue = t["location"]["token"]
        if venue in sched.ignored_venues:
            continue
        if venue not in sched.venues:
            _note_unknown_venue(conn, venue, day)
            continue
        matches = api.matches_of(t["id"])
        if not all(_valid_match(m) for m in matches):
            set_meta(conn, "last_error", f"{_now()} unexpected API format in tournament {t['id']} ({day})")
            stats["skipped"] += 1
            all_finished = False
            continue
        stats["matches"] += store_tournament(conn, sched, t, matches)
        stats["tournaments"] += 1
        if sched.classify(venue, day, to_local(t["start_date"]).strftime("%H:%M"))[0] is None:
            stats["unmatched_blocks"] += 1
        if t.get("status_id") != FINISHED_TOURNAMENT:
            all_finished = False

    today = today_battle_day()
    old_enough = (date.fromisoformat(today) - date.fromisoformat(day)).days
    complete = (all_finished and old_enough >= 1) or (not tournaments and old_enough >= 3)
    conn.execute("INSERT OR REPLACE INTO days(battle_day, complete, fetched_at) VALUES (?,?,?)",
                 (day, int(complete), _now()))
    conn.commit()
    stats["complete"] = complete
    return stats
