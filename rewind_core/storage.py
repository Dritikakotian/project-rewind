"""Database schema, connection safety (WAL, corruption recovery), cleanup and optional encryption."""
import os
import sqlite3
import sys
import time
from pathlib import Path

from .util import *  # noqa: F401,F403

SCHEMA = """
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, summary TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS nodes(id INTEGER PRIMARY KEY, type TEXT, key TEXT, label TEXT, data TEXT, ts TEXT,
                                 UNIQUE(type, key));
CREATE TABLE IF NOT EXISTS edges(src INTEGER, dst INTEGER, rel TEXT, UNIQUE(src, dst, rel));
CREATE TABLE IF NOT EXISTS checkpoints(id INTEGER PRIMARY KEY, ts TEXT, kind TEXT, title TEXT, branch TEXT,
                                       head TEXT, task INTEGER, dirty TEXT, patch TEXT);
CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS ev_ts ON events(ts);
CREATE INDEX IF NOT EXISTS ev_kind ON events(kind);
CREATE INDEX IF NOT EXISTS nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst);
"""


def rewind_home():
    return Path(os.environ.get("REWIND_HOME", Path.home() / ".rewind"))


def _connect(path):
    db = sqlite3.connect(path, timeout=8)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=8000")  # many short-lived processes (editor events) share one file
        try:
            db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        db.executescript(SCHEMA)
        try:
            db.execute("ALTER TABLE checkpoints ADD COLUMN untracked TEXT")
        except sqlite3.OperationalError:
            pass  # column already present
        db.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return db
    except Exception:
        db.close()  # never leave a handle on a damaged file (Windows cannot move it otherwise)
        raise


def open_db(path):
    """Open the project database; a corrupt file is set aside (never deleted) and a fresh one is started."""
    path = Path(path)
    try:
        db = _connect(path)
    except sqlite3.DatabaseError:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        aside = path.with_name(f"{path.name}.corrupt-{stamp}")
        try:
            os.replace(path, aside)
            for ext in ("-wal", "-shm"):
                if Path(str(path) + ext).exists():
                    os.remove(str(path) + ext)
        except OSError:
            pass
        print(f"[rewind] database was unreadable; moved to {aside.name} and started a new one", file=sys.stderr)
        db = _connect(path)
    try:
        os.chmod(path, 0o600)  # best effort; ignored on filesystems without POSIX permissions
    except OSError:
        pass
    return db


def gc(db, keep_patches=30, event_days=90, max_events=5000):
    """Bound growth: drop old events, heavy checkpoint payloads, orphan nodes/edges; then compact."""
    stats = {}
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - event_days * 86400))
    stats["events_old"] = db.execute(
        "DELETE FROM events WHERE ts<? AND id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 500)", (cutoff,)).rowcount
    stats["events_over_cap"] = db.execute(
        "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)", (max_events,)).rowcount
    stats["payloads_dropped"] = db.execute(
        "UPDATE checkpoints SET patch='', untracked='{}' WHERE id NOT IN (SELECT id FROM checkpoints ORDER BY id DESC LIMIT ?) "
        "AND (patch!='' OR untracked NOT IN ('{}',''))", (keep_patches,)).rowcount
    stats["orphan_files"] = db.execute(
        "DELETE FROM nodes WHERE type='file' AND id NOT IN (SELECT src FROM edges) AND id NOT IN (SELECT dst FROM edges)").rowcount
    stats["orphan_edges"] = db.execute(
        "DELETE FROM edges WHERE src NOT IN (SELECT id FROM nodes) OR dst NOT IN (SELECT id FROM nodes)").rowcount
    db.commit()
    db.execute("VACUUM")
    return stats


# ---- optional encryption-at-rest for the heavy payloads (patches, file snapshots) -----------------
def _fernet():
    if os.environ.get("REWIND_ENCRYPT") != "1":
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise RuntimeError("REWIND_ENCRYPT=1 needs the 'cryptography' package (pip install cryptography), "
                           "or unset REWIND_ENCRYPT")
    keyfile = rewind_home() / ".key"
    if not keyfile.exists():
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        keyfile.write_bytes(Fernet.generate_key())
        try:
            os.chmod(keyfile, 0o600)
        except OSError:
            pass
    return Fernet(keyfile.read_bytes())


def seal(text):
    f = _fernet()
    return "enc:" + f.encrypt(text.encode("utf-8")).decode("ascii") if f and text else text


def unseal(text):
    if not text or not text.startswith("enc:"):
        return text
    f = _fernet()
    if not f:
        raise RuntimeError("this data is encrypted; set REWIND_ENCRYPT=1 to read it")
    return f.decrypt(text[4:].encode("ascii")).decode("utf-8")
