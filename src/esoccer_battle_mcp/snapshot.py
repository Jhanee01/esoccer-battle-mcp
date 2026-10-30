"""Data snapshot: build it for the `data-latest` release, download and merge it on first start."""

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import api, db

RELEASE_URL = "https://github.com/Jhanee01/esoccer-battle-mcp/releases/download/data-latest/"
MIN_MISSING_DAYS = 7          # ennyi hiányzó lezárt nap alatt az API-pótlás is gyors, csomag nem kell
MAX_DOWNLOAD = 300 * 1024 * 1024
KEEP_META = ("schema_version", "menetrend_hash")


class SnapshotError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(conn: sqlite3.Connection, out_dir: Path) -> dict:
    """A lezárt napok másolata `battle.db.gz`-be + `manifest.json`. Visszaadja a manifestet."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "battle.db"
    tmp.unlink(missing_ok=True)
    if conn.in_transaction:
        conn.commit()
    conn.execute("VACUUM INTO ?", (str(tmp),))
    snap = sqlite3.connect(tmp)
    try:
        # csak lezárt napok: a félkész nap a felhasználónál úgyis az API-ból frissül
        open_days = "SELECT battle_day FROM days WHERE complete = 1"
        snap.execute(f"DELETE FROM matches WHERE battle_day NOT IN ({open_days})")
        snap.execute(f"DELETE FROM tournaments WHERE battle_day NOT IN ({open_days})")
        snap.execute("DELETE FROM days WHERE complete = 0")
        snap.execute("DELETE FROM unknown_venues")
        snap.execute(f"DELETE FROM meta WHERE key NOT IN ({','.join('?' * len(KEEP_META))})", KEEP_META)
        snap.commit()
        snap.execute("PRAGMA journal_mode=DELETE")
        snap.execute("VACUUM")
        row = snap.execute("SELECT COUNT(*), MIN(battle_day), MAX(battle_day) FROM matches").fetchone()
        days = snap.execute("SELECT COUNT(*), MAX(battle_day) FROM days").fetchone()
        menetrend = snap.execute("SELECT value FROM meta WHERE key = 'menetrend_hash'").fetchone()
    finally:
        snap.close()
    gz = out_dir / "battle.db.gz"
    with open(tmp, "rb") as src, gzip.open(gz, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)
    tmp.unlink()
    manifest = {"schema_version": db.SCHEMA_VERSION, "created_at": _now(),
                "first_day": row[1], "last_complete_day": days[1], "complete_days": days[0],
                "matches": row[0], "menetrend_hash": menetrend[0] if menetrend else None,
                "file": gz.name, "size": gz.stat().st_size, "sha256": _sha256(gz)}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _base_url() -> str:
    return os.environ.get("ESOCCER_BATTLE_SNAPSHOT_URL", RELEASE_URL).rstrip("/") + "/"


def _download(url: str, dest: Path, limit: int = MAX_DOWNLOAD) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": api.HEADERS["User-Agent"]})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = 0
        for chunk in iter(lambda: r.read(1 << 20), b""):
            total += len(chunk)
            if total > limit:
                raise SnapshotError(f"{url} is larger than {limit} bytes")
            f.write(chunk)


def fetch_manifest() -> dict:
    req = urllib.request.Request(_base_url() + "manifest.json", headers={"User-Agent": api.HEADERS["User-Agent"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read(1 << 16))


def _missing_complete(conn: sqlite3.Connection, first: str, last: str) -> int:
    """Hány lezáratlan nap van first..last között (a csomag ennyit tudna pótolni)."""
    from .collector import missing_days
    return len(missing_days(conn, first, last))


def merge(conn: sqlite3.Connection, snap_path: Path) -> int:
    """A csomag beolvasztása meccs-id szerint (duplikáció nincs). Visszaadja a csomag meccsszámát."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("ATTACH DATABASE ? AS snap", (str(snap_path),))
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table in ("tournaments", "matches"):
            conn.execute(f"INSERT OR REPLACE INTO main.{table} SELECT * FROM snap.{table}")
        conn.execute("INSERT OR REPLACE INTO main.days SELECT * FROM snap.days WHERE complete = 1")
        n = conn.execute("SELECT COUNT(*) FROM snap.matches").fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("DETACH DATABASE snap")
    return n


def install(conn: sqlite3.Connection, manifest: dict, workdir: Path) -> int:
    """Letöltés, ellenőrzés (séma, sha256, sqlite-épség), beolvasztás. Hiba → SnapshotError."""
    if manifest.get("schema_version") != db.SCHEMA_VERSION:
        raise SnapshotError(f"snapshot schema {manifest.get('schema_version')} != {db.SCHEMA_VERSION}")
    gz, raw = workdir / "snapshot.db.gz", workdir / "snapshot.db"
    try:
        _download(_base_url() + manifest.get("file", "battle.db.gz"), gz)
        if _sha256(gz) != manifest.get("sha256"):
            raise SnapshotError("snapshot checksum mismatch")
        try:
            with gzip.open(gz, "rb") as src, open(raw, "wb") as dst:
                shutil.copyfileobj(src, dst)
            check = sqlite3.connect(raw)
            try:
                ok = check.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                version = check.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            finally:
                check.close()
        except (OSError, sqlite3.DatabaseError) as e:
            raise SnapshotError(f"snapshot is not a valid database: {e}") from e
        if not ok or not version or version[0] != str(db.SCHEMA_VERSION):
            raise SnapshotError("snapshot failed the integrity check")
        n = merge(conn, raw)
    finally:
        gz.unlink(missing_ok=True)
        raw.unlink(missing_ok=True)
    # a csomag a készítő menetrendjével sorolt; eltérő menetrend → a reclassify újrasorol
    if manifest.get("menetrend_hash") != db.get_meta(conn, "menetrend_hash"):
        db.set_meta(conn, "menetrend_hash", None)
    db.set_meta(conn, "snapshot_day", manifest.get("last_complete_day"))
    db.set_meta(conn, "snapshot_loaded_at", _now())
    return n


def bootstrap(conn: sqlite3.Connection, first_day: str, today: str) -> int:
    """Induláskor: ha sok lezárt nap hiányzik és a csomag pótolná, letölti és beolvasztja.
    Minden hiba csendes (last_error); utána az API-visszatöltés úgyis folytatódik."""
    if os.environ.get("ESOCCER_BATTLE_NO_SNAPSHOT") == "1":
        return 0
    if _missing_complete(conn, first_day, today) <= MIN_MISSING_DAYS:
        return 0
    try:
        manifest = fetch_manifest()
        last = manifest.get("last_complete_day")
        if not last or _missing_complete(conn, first_day, last) <= MIN_MISSING_DAYS:
            return 0
        return install(conn, manifest, db.db_path().parent)
    except Exception as e:
        db.set_meta(conn, "last_error", f"{_now()} snapshot not used, falling back to the API: {e}")
        return 0
