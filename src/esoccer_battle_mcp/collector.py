"""Download Battle days from the API into the local database."""

import os
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone

from . import api
from .db import get_meta, set_meta, today_battle_day
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

    # már lezárt és elmentett tornák meccseit nem kérjük le újra (a friss pótlás így gyors)
    done = {r[0] for r in conn.execute(
        "SELECT id FROM tournaments WHERE battle_day = ? AND status = ?", (day, FINISHED_TOURNAMENT))}

    stats = {"battle_day": day, "tournaments": 0, "matches": 0, "skipped": 0, "unmatched_blocks": 0}
    all_finished = bool(tournaments)
    for t in tournaments:
        venue = t["location"]["token"]
        if venue in sched.ignored_venues:
            continue
        if venue not in sched.venues:
            _note_unknown_venue(conn, venue, day)
            continue
        if t["id"] in done:
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


def reclassify(conn: sqlite3.Connection, sched: Schedule) -> int:
    """Ha a menetrend változott (új kiadás), a tárolt meccsek turnusát/napszakát újraszámolja."""
    if get_meta(conn, "menetrend_hash") == sched.digest:
        return 0
    n = 0
    for t in conn.execute("SELECT id, venue, battle_day, start_local FROM tournaments").fetchall():
        turnus, session = sched.classify(t["venue"], t["battle_day"], t["start_local"][11:16])
        n += conn.execute("UPDATE matches SET turnus = ?, session = ?, format = ? WHERE tournament_id = ?",
                          (turnus, session, sched.format_of(t["venue"]), t["id"])).rowcount
    conn.commit()
    set_meta(conn, "menetrend_hash", sched.digest)
    return n


def missing_days(conn: sqlite3.Connection, first: str, today: str) -> list[str]:
    """A még nem lezárt Battle-napok a legfrissebbtől visszafelé."""
    complete = {r[0] for r in conn.execute("SELECT battle_day FROM days WHERE complete = 1")}
    d, lo, out = date.fromisoformat(today), date.fromisoformat(first), []
    while d >= lo:
        if d.isoformat() not in complete:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return out


LEASE_S = 120      # ennyi ideig érvényes a gyűjtési jog megújítás nélkül


def take_lease(conn: sqlite3.Connection, owner: str) -> bool:
    """Gyűjtési jog: egyszerre csak egy szerver-folyamat gyűjt (több kliens is futhat egyszerre)."""
    now = time.time()
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'sync_lease'").fetchone()
        if row:
            holder, ts = row["value"].rsplit("|", 1)
            if holder != owner and now - float(ts) < LEASE_S:
                return False
        conn.execute("INSERT INTO meta(key, value) VALUES('sync_lease', ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (f"{owner}|{now}",))
        return True
    finally:
        conn.commit()


def release_lease(conn: sqlite3.Connection, owner: str) -> None:
    conn.execute("DELETE FROM meta WHERE key = 'sync_lease' AND value LIKE ?", (owner + "|%",))
    conn.commit()


class Syncer:
    """Háttérszál: 3 percenként friss pótlás (ma + tegnap), közben visszatöltés FIRST_DAY-ig."""

    RECENT_EVERY_S = 180

    def __init__(self, sched: Schedule, first_day: str, connect):
        self.sched, self.first_day, self.connect = sched, first_day, connect
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="esoccer-battle-sync", daemon=True)
        self.owner = f"{os.getpid()}-{id(self)}"
        self.backfill_running = False
        self.is_collector = False
        self._last_recent = 0.0

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stop.set()

    def _refresh_recent(self, conn) -> None:
        today = today_battle_day()
        yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
        try:
            for d in (yesterday, today):
                fetch_day(conn, self.sched, d)
            set_meta(conn, "last_sync", _now())
        except Exception as e:      # hálózati hiba: a toolok a meglévő adatból válaszolnak
            set_meta(conn, "last_error", f"{_now()} recent refresh failed: {e}")
        self._last_recent = time.monotonic()

    def _run(self) -> None:
        conn = self.connect()
        try:
            reclassify(conn, self.sched)
            while not self.stop.is_set():
                self.is_collector = take_lease(conn, self.owner)
                if not self.is_collector:          # egy másik kliens folyamata gyűjt
                    self.stop.wait(LEASE_S / 2)
                    continue
                self._refresh_recent(conn)
                todo = [d for d in missing_days(conn, self.first_day, today_battle_day())
                        if d < (date.fromisoformat(today_battle_day()) - timedelta(days=1)).isoformat()]
                self.backfill_running = bool(todo)
                for d in todo:
                    if self.stop.is_set():
                        return
                    take_lease(conn, self.owner)
                    if time.monotonic() - self._last_recent > self.RECENT_EVERY_S:
                        self._refresh_recent(conn)
                    try:
                        fetch_day(conn, self.sched, d)
                    except Exception as e:
                        set_meta(conn, "last_error", f"{_now()} backfill {d} failed: {e}")
                        self.stop.wait(30)
                self.backfill_running = False
                # a várakozás rövidebb a jog lejáratánál, hogy közben megújítsuk
                wait_s = max(0.0, self.RECENT_EVERY_S - (time.monotonic() - self._last_recent))
                while wait_s > 0 and not self.stop.is_set():
                    take_lease(conn, self.owner)
                    step = min(wait_s, LEASE_S / 2)
                    self.stop.wait(step)
                    wait_s -= step
        finally:
            release_lease(conn, self.owner)
            conn.close()
