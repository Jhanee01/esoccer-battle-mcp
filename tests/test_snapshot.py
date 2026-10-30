import gzip
import json

import pytest

from esoccer_battle_mcp import db, snapshot


def fill(conn):
    """Két lezárt nap (10-27, 10-28) és egy félkész (10-29)."""
    for tid, day, complete in ((1, "2026-10-27", 1), (2, "2026-10-28", 1), (3, "2026-10-29", 0)):
        conn.execute("INSERT INTO tournaments(id, venue, battle_day, start_local, status) VALUES (?,?,?,?,4)",
                     (tid, "Wembley", day, day + " 07:00"))
        conn.execute("INSERT INTO matches(id, tournament_id, battle_day, venue, player1, player2, score1, score2)"
                     " VALUES (?,?,?,?,?,?,?,?)", (tid * 10, tid, day, "Wembley", "Alpha", "Beta", 2, 1))
        conn.execute("INSERT INTO days VALUES (?,?, '')", (day, complete))
    db.set_meta(conn, "menetrend_hash", "h1")
    db.set_meta(conn, "sync_lease", "someone|1")
    conn.commit()


@pytest.fixture
def built(tmp_path):
    src = db.connect(tmp_path / "src.db")
    fill(src)
    out = tmp_path / "release"
    manifest = snapshot.build(src, out)
    src.close()
    return out, manifest


def test_build_keeps_only_complete_days(built):
    out, manifest = built
    assert (manifest["matches"], manifest["complete_days"], manifest["last_complete_day"]) == (2, 2, "2026-10-28")
    assert manifest["schema_version"] == db.SCHEMA_VERSION and manifest["menetrend_hash"] == "h1"
    assert json.loads((out / "manifest.json").read_text())["sha256"] == manifest["sha256"]
    assert not (out / "battle.db").exists()


def test_install_merges_without_duplicates(built, tmp_path, monkeypatch):
    out, manifest = built
    monkeypatch.setenv("ESOCCER_BATTLE_SNAPSHOT_URL", out.as_uri())
    conn = db.connect(tmp_path / "user.db")
    conn.execute("INSERT INTO matches(id, battle_day, venue) VALUES (20, '2026-10-28', 'Wembley')")
    conn.commit()
    assert snapshot.install(conn, snapshot.fetch_manifest(), tmp_path) == 2
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 2
    assert [r[0] for r in conn.execute("SELECT battle_day FROM days ORDER BY 1")] == ["2026-10-27", "2026-10-28"]
    assert db.get_meta(conn, "snapshot_day") == "2026-10-28"
    assert db.get_meta(conn, "sync_lease") is None             # helyi állapot nem jön át
    assert db.get_meta(conn, "menetrend_hash") is None         # más menetrenddel sorolt → reclassify
    assert not list(tmp_path.glob("snapshot.db*"))


def test_corrupt_snapshot_rejected(built, tmp_path, monkeypatch):
    out, manifest = built
    monkeypatch.setenv("ESOCCER_BATTLE_SNAPSHOT_URL", out.as_uri())
    with gzip.open(out / "battle.db.gz", "ab") as f:
        f.write(b"garbage")
    conn = db.connect(tmp_path / "user.db")
    with pytest.raises(snapshot.SnapshotError, match="checksum"):
        snapshot.install(conn, manifest, tmp_path)
    with pytest.raises(snapshot.SnapshotError, match="schema"):
        snapshot.install(conn, {**manifest, "schema_version": 99}, tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0


def test_bootstrap_only_when_many_days_missing(built, tmp_path, monkeypatch):
    out, _ = built
    monkeypatch.setenv("ESOCCER_BATTLE_SNAPSHOT_URL", out.as_uri())
    monkeypatch.setenv("ESOCCER_BATTLE_DB", str(tmp_path / "user.db"))
    monkeypatch.delenv("ESOCCER_BATTLE_NO_SNAPSHOT")
    conn = db.connect()
    # a csomag csak 2 napot pótolna (10-27, 10-28) → nem éri meg, az API gyorsabb
    assert snapshot.bootstrap(conn, "2026-10-27", "2026-10-30") == 0
    monkeypatch.setattr(snapshot, "MIN_MISSING_DAYS", 1)
    assert snapshot.bootstrap(conn, "2026-10-27", "2026-10-30") == 2
    assert snapshot.bootstrap(conn, "2026-10-27", "2026-10-30") == 0   # már megvan


def test_bootstrap_failure_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("ESOCCER_BATTLE_SNAPSHOT_URL", (tmp_path / "nothing").as_uri())
    monkeypatch.setenv("ESOCCER_BATTLE_DB", str(tmp_path / "user.db"))
    monkeypatch.delenv("ESOCCER_BATTLE_NO_SNAPSHOT")
    monkeypatch.setattr(snapshot, "MIN_MISSING_DAYS", 0)
    conn = db.connect()
    assert snapshot.bootstrap(conn, "2026-10-27", "2026-10-30") == 0
    assert "falling back to the API" in db.get_meta(conn, "last_error")
