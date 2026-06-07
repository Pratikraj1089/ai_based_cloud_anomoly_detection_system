"""
============================================================
database.py — SQLite Database Handler (AIOps Edition)
Project : Smart Cloud Pulse AI Monitor
Org     : NTPL Digital Private Limited, Noida
Group   : CU - MCA - Group-4
------------------------------------------------------------
All SQLite operations:
  - Schema creation + safe migrations (never drops existing data)
  - Metrics, Anomalies, Alerts, Servers CRUD
  - NEW: Remediation logs, Server model registry, Analytics queries
============================================================
"""

import sqlite3
import json
import logging
import os
import sys

# Allow importing config from project root regardless of working directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH

logger = logging.getLogger(__name__)


# ─── Connection Helper ────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Return a new SQLite connection with Row factory enabled."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL") # better concurrency
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─── Schema Initialisation ────────────────────────────────────────────────────

# ─── Safe column-addition helper ─────────────────────────────────────────────

def _safe_add_column(conn, table: str, column: str, col_type: str,
                     default: str = "") -> None:
    """Add a column only if it doesn't already exist (safe migration)."""
    rows     = conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = [r["name"] for r in rows]
    if column not in existing:
        clause = f" DEFAULT {default}" if default else ""
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}{clause}")
        logger.info("Migration: added '%s' to '%s'", column, table)


def init_db() -> None:
    """Create all tables and run safe column migrations."""
    ddl = """
    CREATE TABLE IF NOT EXISTS metrics (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp    TEXT    NOT NULL DEFAULT (datetime('now')),
        cpu          REAL    NOT NULL,
        ram          REAL    NOT NULL,
        disk         REAL    NOT NULL,
        network_in   REAL    NOT NULL DEFAULT 0,
        network_out  REAL    NOT NULL DEFAULT 0,
        processes    INTEGER NOT NULL DEFAULT 0,
        uptime       REAL    NOT NULL DEFAULT 0,
        server_id    INTEGER
    );

    CREATE TABLE IF NOT EXISTS anomalies (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp      TEXT    NOT NULL DEFAULT (datetime('now')),
        metric_values  TEXT    NOT NULL,
        anomaly_score  REAL    NOT NULL,
        severity       TEXT    NOT NULL,
        server_id      INTEGER,
        reasons        TEXT    DEFAULT '[]',
        probable_cause TEXT    DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS alerts (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT    NOT NULL DEFAULT (datetime('now')),
        message   TEXT    NOT NULL,
        status    TEXT    NOT NULL CHECK(status IN ('sent','pending','failed')),
        server_id INTEGER,
        channel   TEXT    DEFAULT 'console'
    );

    CREATE TABLE IF NOT EXISTS servers (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        name     TEXT    NOT NULL,
        ip       TEXT    NOT NULL,
        port     INTEGER NOT NULL DEFAULT 22,
        username TEXT    NOT NULL
    );

    -- NEW: auto-remediation action audit log
    CREATE TABLE IF NOT EXISTS remediation_logs (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp    TEXT    NOT NULL DEFAULT (datetime('now')),
        server_id    INTEGER,
        action       TEXT    NOT NULL,
        target       TEXT    NOT NULL,
        result       TEXT    NOT NULL,
        initiated_by TEXT    NOT NULL DEFAULT 'auto'
    );

    -- NEW: per-server model training state
    CREATE TABLE IF NOT EXISTS server_models (
        server_id     INTEGER PRIMARY KEY,
        is_trained    INTEGER NOT NULL DEFAULT 0,
        trained_at    TEXT,
        sample_count  INTEGER NOT NULL DEFAULT 0,
        feature_count INTEGER NOT NULL DEFAULT 0
    );
    """
    try:
        with get_connection() as conn:
            conn.executescript(ddl)

            # Column migrations (safe — only adds, never removes)
            _safe_add_column(conn, "metrics",   "server_id",      "INTEGER")
            _safe_add_column(conn, "anomalies",  "server_id",      "INTEGER")
            _safe_add_column(conn, "anomalies",  "reasons",        "TEXT", "'[]'")
            _safe_add_column(conn, "anomalies",  "probable_cause", "TEXT", "''")
            _safe_add_column(conn, "anomalies",  "network_in_ratio", "REAL", "0.0")
            _safe_add_column(conn, "anomalies",  "network_out_ratio", "REAL", "0.0")
            _safe_add_column(conn, "alerts",     "server_id",      "INTEGER")
            _safe_add_column(conn, "alerts",     "channel",        "TEXT", "'console'")
            _safe_add_column(conn, "alerts",     "recipient",      "TEXT", "''")
            _safe_add_column(conn, "alerts",     "subject",        "TEXT", "''")
            _safe_add_column(conn, "alerts",     "error_message",  "TEXT", "''")

            # Legacy CHECK-constraint migration
            _migrate_anomalies_constraint(conn)

        logger.info("Database initialised at %s", DB_PATH)
    except Exception as exc:
        logger.exception("Failed to initialise database: %s", exc)
        raise


def _migrate_anomalies_constraint(conn) -> None:
    """Remove legacy CHECK(severity IN ...) from anomalies table if present."""
    try:
        conn.execute(
            "INSERT INTO anomalies (metric_values, anomaly_score, severity) "
            "VALUES ('{}', 0.0, '__test__')"
        )
        conn.execute("DELETE FROM anomalies WHERE severity='__test__'")
    except sqlite3.IntegrityError:
        logger.info("Migrating anomalies table to remove CHECK constraint...")
        conn.execute("ALTER TABLE anomalies RENAME TO anomalies_old")
        conn.execute("""
            CREATE TABLE anomalies (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp      TEXT NOT NULL DEFAULT (datetime('now')),
                metric_values  TEXT NOT NULL,
                anomaly_score  REAL NOT NULL,
                severity       TEXT NOT NULL,
                server_id      INTEGER,
                reasons        TEXT DEFAULT '[]',
                probable_cause TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            INSERT INTO anomalies (id,timestamp,metric_values,anomaly_score,severity,server_id)
            SELECT id,timestamp,metric_values,anomaly_score,severity,server_id FROM anomalies_old
        """)
        conn.execute("DROP TABLE anomalies_old")
        logger.info("Anomalies constraint migration complete")



# ─── Metrics ─────────────────────────────────────────────────────────────────

def insert_metric(data: dict) -> int:
    """
    Insert a single metric reading.
    Returns the new row id.
    """
    data.setdefault("server_id", None)
    sql = """
    INSERT INTO metrics (timestamp, cpu, ram, disk, network_in, network_out, processes, uptime, server_id)
    VALUES (:timestamp, :cpu, :ram, :disk, :network_in, :network_out, :processes, :uptime, :server_id)
    """
    try:
        with get_connection() as conn:
            cursor = conn.execute(sql, data)
            return cursor.lastrowid
    except Exception as exc:
        logger.exception("insert_metric failed: %s", exc)
        raise


def get_latest_metrics(limit: int = 100, server_id: int = None) -> list[dict]:
    """Return the most recent *limit* metric rows as a list of dicts for a specific server."""
    try:
        if not server_id:
            sql = """
            SELECT * FROM metrics
            WHERE server_id IS NULL OR server_id = 0
            ORDER BY id DESC
            LIMIT ?
            """
            params = (limit,)
        else:
            sql = """
            SELECT * FROM metrics
            WHERE server_id = ?
            ORDER BY id DESC
            LIMIT ?
            """
            params = (server_id, limit)
            
        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in reversed(rows)]  # chronological order
    except Exception as exc:
        logger.exception("get_latest_metrics failed: %s", exc)
        return []


def get_metrics_for_training(limit: int = 500, server_id: int = None) -> list[dict]:
    """Return up to *limit* recent metrics for model training on a specific server."""
    try:
        if not server_id:
            sql = "SELECT * FROM metrics WHERE server_id IS NULL OR server_id = 0 ORDER BY id DESC LIMIT ?"
            params = (limit,)
        else:
            sql = "SELECT * FROM metrics WHERE server_id = ? ORDER BY id DESC LIMIT ?"
            params = (server_id, limit)
            
        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.exception("get_metrics_for_training failed: %s", exc)
        return []


def count_metrics(server_id: int = None) -> int:
    """Return total number of metric readings stored for a specific server."""
    try:
        if not server_id:
            sql = "SELECT COUNT(*) as cnt FROM metrics WHERE server_id IS NULL OR server_id = 0"
            params = ()
        else:
            sql = "SELECT COUNT(*) as cnt FROM metrics WHERE server_id = ?"
            params = (server_id,)
            
        with get_connection() as conn:
            row = conn.execute(sql, params).fetchone()
            return row["cnt"]
    except Exception as exc:
        logger.exception("count_metrics failed: %s", exc)
        return 0


# ─── Anomalies ────────────────────────────────────────────────────────────────

def insert_anomaly(metric_data: dict, score: float, severity: str,
                   server_id: int = None, reasons: list = None,
                   probable_cause: str = "", network_in_ratio: float = 0.0,
                   network_out_ratio: float = 0.0) -> int:
    """Log a detected anomaly with optional explanation fields."""
    sql = """
    INSERT INTO anomalies (timestamp, metric_values, anomaly_score, severity,
                           server_id, reasons, probable_cause,
                           network_in_ratio, network_out_ratio)
    VALUES (datetime('now'), :metric_values, :score, :severity,
            :server_id, :reasons, :probable_cause,
            :network_in_ratio, :network_out_ratio)
    """
    try:
        with get_connection() as conn:
            return conn.execute(sql, {
                "metric_values":      json.dumps(metric_data),
                "score":              score,
                "severity":           severity,
                "server_id":          server_id,
                "reasons":            json.dumps(reasons or []),
                "probable_cause":     probable_cause or "",
                "network_in_ratio":   network_in_ratio,
                "network_out_ratio":  network_out_ratio,
            }).lastrowid
    except Exception as exc:
        logger.exception("insert_anomaly failed: %s", exc)
        raise


def get_anomalies(limit: int = 50, server_id: int = None) -> list[dict]:
    """Return the most recent anomaly records; parses reasons JSON automatically."""
    try:
        if not server_id:
            sql    = "SELECT * FROM anomalies WHERE server_id IS NULL OR server_id=0 ORDER BY id DESC LIMIT ?"
            params = (limit,)
        else:
            sql    = "SELECT * FROM anomalies WHERE server_id=? ORDER BY id DESC LIMIT ?"
            params = (server_id, limit)
        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["reasons"] = json.loads(d.get("reasons") or "[]")
            except Exception:
                d["reasons"] = []
            result.append(d)
        return result
    except Exception as exc:
        logger.exception("get_anomalies failed: %s", exc)
        return []



# ─── Alerts ───────────────────────────────────────────────────────────────────

def insert_alert(message: str, status: str = "pending",
                 server_id: int = None, channel: str = "console") -> int:
    """Log a new alert entry with channel and server info."""
    sql = """
    INSERT INTO alerts (timestamp, message, status, server_id, channel)
    VALUES (datetime('now'), :message, :status, :server_id, :channel)
    """
    try:
        with get_connection() as conn:
            return conn.execute(sql, {
                "message": message, "status": status,
                "server_id": server_id, "channel": channel,
            }).lastrowid
    except Exception as exc:
        logger.exception("insert_alert failed: %s", exc)
        raise


def update_alert_status(alert_id: int, status: str) -> None:
    """Update the delivery status of an existing alert."""
    try:
        with get_connection() as conn:
            conn.execute("UPDATE alerts SET status=? WHERE id=?", (status, alert_id))
    except Exception as exc:
        logger.exception("update_alert_status failed: %s", exc)


def get_alerts(limit: int = 50) -> list[dict]:
    """Return the most recent *limit* alert records."""
    try:
        with get_connection() as conn:
            return [dict(r) for r in
                    conn.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    except Exception as exc:
        logger.exception("get_alerts failed: %s", exc)
        return []


# ─── Servers ──────────────────────────────────────────────────────────────────

def add_server(name: str, ip: str, port: int, username: str) -> int:
    """Add a new server for SSH monitoring."""
    sql = """
    INSERT INTO servers (name, ip, port, username)
    VALUES (:name, :ip, :port, :username)
    """
    try:
        with get_connection() as conn:
            cursor = conn.execute(sql, {"name": name, "ip": ip, "port": port, "username": username})
            return cursor.lastrowid
    except Exception as exc:
        logger.exception("add_server failed: %s", exc)
        raise

def get_servers() -> list[dict]:
    """Retrieve all monitored servers."""
    sql = "SELECT id, name, ip, port, username FROM servers ORDER BY id DESC"
    try:
        with get_connection() as conn:
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.exception("get_servers failed: %s", exc)
        return []


# ─── Remediation Logs (NEW) ───────────────────────────────────────────────────

def insert_remediation_log(server_id, action: str, target: str,
                            result: str, initiated_by: str = "auto") -> int:
    """Audit log entry for every auto-remediation action."""
    sql = """
    INSERT INTO remediation_logs (timestamp,server_id,action,target,result,initiated_by)
    VALUES (datetime('now'),:server_id,:action,:target,:result,:initiated_by)
    """
    try:
        with get_connection() as conn:
            return conn.execute(sql, {
                "server_id": server_id, "action": action,
                "target": target, "result": result, "initiated_by": initiated_by,
            }).lastrowid
    except Exception as exc:
        logger.exception("insert_remediation_log failed: %s", exc); raise


def get_remediation_logs(limit: int = 50, server_id=None) -> list[dict]:
    try:
        if server_id:
            sql, p = ("SELECT * FROM remediation_logs WHERE server_id=? ORDER BY id DESC LIMIT ?",
                      (server_id, limit))
        else:
            sql, p = "SELECT * FROM remediation_logs ORDER BY id DESC LIMIT ?", (limit,)
        with get_connection() as conn:
            return [dict(r) for r in conn.execute(sql, p).fetchall()]
    except Exception as exc:
        logger.exception("get_remediation_logs failed: %s", exc); return []


# ─── Server Model Registry (NEW) ─────────────────────────────────────────────

def upsert_server_model(server_id: int, is_trained: bool,
                         sample_count: int, feature_count: int) -> None:
    """Track per-server model training state (UPSERT)."""
    sql = """
    INSERT INTO server_models (server_id,is_trained,trained_at,sample_count,feature_count)
    VALUES (:sid,:trained,datetime('now'),:samples,:features)
    ON CONFLICT(server_id) DO UPDATE SET
        is_trained=excluded.is_trained, trained_at=excluded.trained_at,
        sample_count=excluded.sample_count, feature_count=excluded.feature_count
    """
    try:
        with get_connection() as conn:
            conn.execute(sql, {"sid": server_id, "trained": int(is_trained),
                               "samples": sample_count, "features": feature_count})
    except Exception as exc:
        logger.exception("upsert_server_model failed: %s", exc)


def get_server_model_registry() -> list[dict]:
    try:
        with get_connection() as conn:
            return [dict(r) for r in
                    conn.execute("SELECT * FROM server_models ORDER BY server_id").fetchall()]
    except Exception as exc:
        logger.exception("get_server_model_registry failed: %s", exc); return []


# ─── Analytics Queries (NEW) ─────────────────────────────────────────────────

def get_anomaly_count_per_day(days: int = 30) -> list[dict]:
    """Daily anomaly counts for the last N days."""
    sql = """
    SELECT date(timestamp) as day, COUNT(*) as count
    FROM   anomalies
    WHERE  timestamp >= datetime('now', :offset)
    GROUP  BY day ORDER BY day ASC
    """
    try:
        with get_connection() as conn:
            return [dict(r) for r in conn.execute(sql, {"offset": f"-{days} days"}).fetchall()]
    except Exception as exc:
        logger.exception("get_anomaly_count_per_day failed: %s", exc); return []


def get_severity_distribution() -> list[dict]:
    """Count of anomalies grouped by severity label."""
    try:
        with get_connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT severity, COUNT(*) as count FROM anomalies GROUP BY severity ORDER BY count DESC"
            ).fetchall()]
    except Exception as exc:
        logger.exception("get_severity_distribution failed: %s", exc); return []


def get_top_servers_by_anomalies(limit: int = 10) -> list[dict]:
    """Servers with the highest anomaly counts."""
    sql = """
    SELECT a.server_id,
           COALESCE(s.name,'Local') as server_name,
           COUNT(*) as anomaly_count
    FROM   anomalies a
    LEFT   JOIN servers s ON s.id = a.server_id
    GROUP  BY a.server_id
    ORDER  BY anomaly_count DESC LIMIT ?
    """
    try:
        with get_connection() as conn:
            return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]
    except Exception as exc:
        logger.exception("get_top_servers_by_anomalies failed: %s", exc); return []


def get_cause_distribution() -> list[dict]:
    """Count of anomalies grouped by probable_cause."""
    sql = """
    SELECT probable_cause, COUNT(*) as count
    FROM   anomalies
    WHERE  probable_cause IS NOT NULL AND probable_cause != ''
    GROUP  BY probable_cause ORDER BY count DESC
    """
    try:
        with get_connection() as conn:
            return [dict(r) for r in conn.execute(sql).fetchall()]
    except Exception as exc:
        logger.exception("get_cause_distribution failed: %s", exc); return []


def get_email_alert_stats() -> dict:
    """Return stats for email alerts: last sent timestamp, total sent, total failed, and counts for today."""
    stats = {
        "last_email_sent": None,
        "total_sent": 0,
        "total_failed": 0,
        "sent_today": 0,
        "failed_today": 0
    }
    try:
        with get_connection() as conn:
            # Last sent email
            row = conn.execute("""
                SELECT timestamp FROM alerts 
                WHERE channel='email' AND status='sent' 
                ORDER BY id DESC LIMIT 1
            """).fetchone()
            if row:
                stats["last_email_sent"] = row["timestamp"]

            # Overall counts
            row_sent = conn.execute("SELECT COUNT(*) as cnt FROM alerts WHERE channel='email' AND status='sent'").fetchone()
            stats["total_sent"] = row_sent["cnt"] if row_sent else 0

            row_failed = conn.execute("SELECT COUNT(*) as cnt FROM alerts WHERE channel='email' AND status='failed'").fetchone()
            stats["total_failed"] = row_failed["cnt"] if row_failed else 0

            # Today's counts (SQLite date('now') works with UTC, let's use date(timestamp) = date('now'))
            row_today_sent = conn.execute("""
                SELECT COUNT(*) as cnt FROM alerts 
                WHERE channel='email' AND status='sent' AND date(timestamp) = date('now')
            """).fetchone()
            stats["sent_today"] = row_today_sent["cnt"] if row_today_sent else 0

            row_today_failed = conn.execute("""
                SELECT COUNT(*) as cnt FROM alerts 
                WHERE channel='email' AND status='failed' AND date(timestamp) = date('now')
            """).fetchone()
            stats["failed_today"] = row_today_failed["cnt"] if row_today_failed else 0

    except Exception as exc:
        logger.exception("get_email_alert_stats failed: %s", exc)
    return stats
