"""SQLite storage: schema, connection, meta values and the shared query filters."""

import os
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .schedule import DAY_START_MIN, TZ, Schedule

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS tournaments (
    id INTEGER PRIMARY KEY,
    name TEXT, league TEXT, venue TEXT,
    start_utc TEXT, start_local TEXT, battle_day TEXT,
    status INTEGER, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    tournament_id INTEGER REFERENCES tournaments(id),
    battle_day TEXT, start_utc TEXT, local_date TEXT, local_time TEXT,
    venue TEXT, format TEXT, turnus INTEGER, session TEXT,
    player1 TEXT, team1 TEXT, player2 TEXT, team2 TEXT,
    score1 INTEGER, score2 INTEGER, corners1 INTEGER, corners2 INTEGER
);
CREATE INDEX IF NOT EXISTS ix_matches_day ON matches(battle_day);
CREATE INDEX IF NOT EXISTS ix_matches_p1 ON matches(player1 COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_matches_p2 ON matches(player2 COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_matches_t1 ON matches(team1 COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_matches_t2 ON matches(team2 COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_matches_venue_day ON matches(venue, battle_day);
CREATE INDEX IF NOT EXISTS ix_matches_tournament ON matches(tournament_id);
CREATE TABLE IF NOT EXISTS days (
    battle_day TEXT PRIMARY KEY, complete INTEGER, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS unknown_venues (
    venue TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, tournaments INTEGER
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

SESSIONS = ("day", "afternoon", "night")
FORMATS = ("8min", "12min")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def db_path() -> Path:
    env = os.environ.get("ESOCCER_BATTLE_DB")
    return Path(env) if env else Path.home() / ".esoccer-battle-mcp" / "battle.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    # a WAL-váltás nem várakozik zárolásnál (párhuzamos első megnyitás) → csak ha kell, újrapróbálva
    for attempt in range(50):
        try:
            if conn.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                conn.execute("PRAGMA journal_mode=WAL")
            break
        except sqlite3.OperationalError:
            if attempt == 49:
                raise
            time.sleep(0.1)
    conn.executescript(SCHEMA)
    if get_meta(conn, "schema_version") is None:
        set_meta(conn, "schema_version", str(SCHEMA_VERSION))
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    conn.commit()


def today_battle_day(now: datetime | None = None) -> str:
    now = now or datetime.now(TZ)
    d = now.date()
    if now.hour * 60 + now.minute < DAY_START_MIN:
        d -= timedelta(days=1)
    return d.isoformat()


def _check_day(name: str, value: str) -> None:
    if not _DAY.match(value):
        raise ValueError(f"{name} must be a date in YYYY-MM-DD format, got {value!r}")
    date.fromisoformat(value)


def build_filters(sched: Schedule, *, since: str | None = None, until: str | None = None,
                  last_days: int | None = None, venue: str | None = None,
                  format: str | None = None, session: str | None = None,
                  turnus: int | None = None, time_from: str | None = None,
                  time_to: str | None = None, era: str = "current",
                  alias: str = "") -> tuple[str, list, dict]:
    """Közös szűrők → (WHERE-feltétel, paraméterek, alkalmazott szűrők). Hibás érték → ValueError."""
    col = (alias + ".") if alias else ""
    where, params, applied = [], [], {}

    if era not in ("all", "current") and sched.era(era) is None:
        valid = ", ".join(["current", "all"] + [e["id"] for e in sched.eras])
        raise ValueError(f"Unknown era {era!r}. Valid values: {valid}")
    if era != "all":
        e = sched.era(era)
        applied["era"] = e["id"]
        where.append(f"{col}battle_day >= ?")
        params.append(e["from"])
        if e.get("to"):
            where.append(f"{col}battle_day <= ?")
            params.append(e["to"])
    else:
        applied["era"] = "all"

    if last_days is not None:
        if last_days < 1:
            raise ValueError("last_days must be at least 1")
        since = (date.fromisoformat(today_battle_day()) - timedelta(days=last_days - 1)).isoformat()
        applied["last_days"] = last_days
    for name, value, op in (("since", since, ">="), ("until", until, "<=")):
        if value:
            _check_day(name, value)
            where.append(f"{col}battle_day {op} ?")
            params.append(value)
            applied[name] = value

    if venue:
        if venue not in sched.venues:
            raise ValueError(f"Unknown venue {venue!r}. Valid values: {', '.join(sched.venues)}")
        where.append(f"{col}venue = ?")
        params.append(venue)
        applied["venue"] = venue
    if format:
        if format not in FORMATS:
            raise ValueError(f"Unknown format {format!r}. Valid values: {', '.join(FORMATS)}")
        where.append(f"{col}format = ?")
        params.append(format)
        applied["format"] = format
    if session:
        if session not in SESSIONS:
            raise ValueError(f"Unknown session {session!r}. Valid values: {', '.join(SESSIONS)}")
        where.append(f"{col}session = ?")
        params.append(session)
        applied["session"] = session
    if turnus is not None:
        if not 1 <= turnus <= 11:
            raise ValueError("turnus must be between 1 and 11")
        where.append(f"{col}turnus = ?")
        params.append(turnus)
        applied["turnus"] = turnus

    if time_from or time_to:
        for name, value in (("time_from", time_from), ("time_to", time_to)):
            if value and not _HHMM.match(value):
                raise ValueError(f"{name} must be HH:MM (24h), got {value!r}")
        lo, hi = time_from or "00:00", time_to or "23:59"
        if lo <= hi:
            where.append(f"{col}local_time BETWEEN ? AND ?")
        else:   # éjfélen átnyúló ablak, pl. 22:00 → 02:00
            where.append(f"({col}local_time >= ? OR {col}local_time <= ?)")
        params += [lo, hi]
        applied["time_from"], applied["time_to"] = lo, hi

    return (" AND ".join(where) or "1=1"), params, applied
