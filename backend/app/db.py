"""Database connection, versioned migrations, and tamper-evident audit logging."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = BASE_DIR / "migrations"
DEFAULT_DB_PATH = BASE_DIR / "coldchain.db"


def get_db_path() -> Path:
    """Return the configured SQLite database path."""
    return Path(os.environ.get("COLDCHAIN_DB_PATH", str(DEFAULT_DB_PATH)))


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def create_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a SQLite connection configured with WAL mode and foreign keys."""
    target = db_path or get_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


@contextmanager
def db_session(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a committed/rolled-back SQLite connection."""
    conn = create_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_migrations(
    db_path: Optional[Path] = None,
    migrations_dir: Path = MIGRATIONS_DIR,
    max_version: Optional[str] = None,
) -> list[str]:
    """Execute pending SQL migrations in lexical order without data loss."""
    applied_now: list[str] = []
    with db_session(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        already_applied = {r["version"] for r in rows}

        for sql_file in sorted(migrations_dir.glob("*.sql")):
            version = sql_file.name
            if max_version and version > max_version:
                continue
            if version in already_applied:
                continue
            sql = sql_file.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, utc_now_iso()),
            )
            applied_now.append(version)
    return applied_now


def _compute_audit_hash(
    prev_hash: str,
    ts: str,
    log_type: str,
    shipment_id: str,
    temp: Optional[float],
    detail: str,
    lat: Optional[float],
    lng: Optional[float],
) -> str:
    """Compute SHA-256 hash linking an audit record to the previous record."""
    temp_s = "" if temp is None else f"{float(temp):.2f}"
    lat_s = "" if lat is None else f"{float(lat):.6f}"
    lng_s = "" if lng is None else f"{float(lng):.6f}"
    payload = f"{prev_hash}|{ts}|{log_type}|{shipment_id}|{temp_s}|{detail}|{lat_s}|{lng_s}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def append_audit_log(
    conn: sqlite3.Connection,
    *,
    ts: str,
    log_type: str,
    shipment_id: str,
    detail: str,
    temp: Optional[float] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
) -> None:
    """Append a tamper-evident hash-chained record to audit_logs."""
    last = conn.execute(
        "SELECT record_hash FROM audit_logs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash = last["record_hash"] if last else "GENESIS"
    record_hash = _compute_audit_hash(
        prev_hash, ts, log_type, shipment_id, temp, detail, lat, lng
    )
    conn.execute(
        """
        INSERT INTO audit_logs (ts, type, shipment_id, temp, detail, lat, lng, prev_hash, record_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (ts, log_type, shipment_id, temp, detail, lat, lng, prev_hash, record_hash),
    )


def verify_audit_chain(conn: sqlite3.Connection) -> dict[str, object]:
    """Verify integrity of the entire audit_logs hash chain."""
    rows = conn.execute(
        "SELECT id, ts, type, shipment_id, temp, detail, lat, lng, prev_hash, record_hash "
        "FROM audit_logs ORDER BY id ASC"
    ).fetchall()
    expected_prev = "GENESIS"
    for row in rows:
        if row["prev_hash"] != expected_prev:
            return {"valid": False, "checked": len(rows), "broken_id": row["id"]}
        expected_hash = _compute_audit_hash(
            row["prev_hash"],
            row["ts"],
            row["type"],
            row["shipment_id"],
            row["temp"],
            row["detail"],
            row["lat"],
            row["lng"],
        )
        if row["record_hash"] != expected_hash:
            return {"valid": False, "checked": len(rows), "broken_id": row["id"]}
        expected_prev = row["record_hash"]
    return {"valid": True, "checked": len(rows), "head_hash": expected_prev}
