"""
============================================================
database.py — SQLite Database Handler
Project : AI-Based Anomaly Detection System for Cloud Resource Monitoring
Org     : NTPL Digital Private Limited, Noida
Group   : CU - MCA - Group-4
------------------------------------------------------------
Handles all SQLite operations:
  - Table creation / migration
  - Inserting metrics and anomaly records
  - Querying latest metrics, anomaly history, and alerts
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

def init_db() -> None:
    """Create all tables if they do not already exist."""
    ddl = """
    -- Table 1: raw system metrics sent by the agent
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

    -- Table 2: detected anomalies with severity
    CREATE TABLE IF NOT EXISTS anomalies (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp     TEXT    NOT NULL DEFAULT (datetime('now')),
        metric_values TEXT    NOT NULL,  -- JSON snapshot of the reading
        anomaly_score REAL    NOT NULL,
        severity      TEXT    NOT NULL,
        server_id     INTEGER
    );

    -- Table 3: alert log (email / webhook events)
    CREATE TABLE IF NOT EXISTS alerts (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT    NOT NULL DEFAULT (datetime('now')),
        message   TEXT    NOT NULL,
        status    TEXT    NOT NULL CHECK(status IN ('sent','pending','failed'))
    );

    -- Table 4: monitored servers (for agentless ssh monitoring)
    CREATE TABLE IF NOT EXISTS servers (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        name     TEXT    NOT NULL,
        ip       TEXT    NOT NULL,
        port     INTEGER NOT NULL DEFAULT 22,
        username TEXT    NOT NULL
    );
    """
    try:
        with get_connection() as conn:
            conn.executescript(ddl)
            
            # Dynamic Migration: Check if server_id column exists in metrics
            cursor = conn.execute("PRAGMA table_info(metrics)")
            columns = [row["name"] for row in cursor.fetchall()]
            if "server_id" not in columns:
                conn.execute("ALTER TABLE metrics ADD COLUMN server_id INTEGER")
                logger.info("Migrated metrics table: added server_id column")

            # Dynamic Migration: Check if server_id column exists in anomalies
            cursor = conn.execute("PRAGMA table_info(anomalies)")
            columns = [row["name"] for row in cursor.fetchall()]
            if "server_id" not in columns:
                conn.execute("ALTER TABLE anomalies ADD COLUMN server_id INTEGER")
                logger.info("Migrated anomalies table: added server_id column")

            # Dynamic Migration: Remove CHECK constraint on anomalies table if it exists
            # We verify this by attempting a temp insert with 'danger' severity
            try:
                conn.execute("INSERT INTO anomalies (metric_values, anomaly_score, severity) VALUES ('{}', 0.0, 'danger')")
                conn.rollback()
            except sqlite3.IntegrityError:
                # The CHECK constraint failed, migrate the table
                logger.info("Migrating anomalies table to remove CHECK constraint...")
                conn.execute("ALTER TABLE anomalies RENAME TO anomalies_old")
                conn.execute("""
                CREATE TABLE anomalies (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     TEXT    NOT NULL DEFAULT (datetime('now')),
                    metric_values TEXT    NOT NULL,
                    anomaly_score REAL    NOT NULL,
                    severity      TEXT    NOT NULL,
                    server_id     INTEGER
                )
                """)
                # Populate new table from old data
                conn.execute("""
                INSERT INTO anomalies (id, timestamp, metric_values, anomaly_score, severity, server_id)
                SELECT id, timestamp, metric_values, anomaly_score, severity, server_id FROM anomalies_old
                """)
                conn.execute("DROP TABLE anomalies_old")
                logger.info("Anomalies table migration complete")
                
        logger.info("Database initialised at %s", DB_PATH)
    except Exception as exc:
        logger.exception("Failed to initialise database: %s", exc)
        raise


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

def insert_anomaly(metric_data: dict, score: float, severity: str, server_id: int = None) -> int:
    """
    Log a detected anomaly.
    Returns the new row id.
    """
    sql = """
    INSERT INTO anomalies (timestamp, metric_values, anomaly_score, severity, server_id)
    VALUES (datetime('now'), :metric_values, :anomaly_score, :severity, :server_id)
    """
    try:
        with get_connection() as conn:
            cursor = conn.execute(sql, {
                "metric_values": json.dumps(metric_data),
                "anomaly_score": score,
                "severity":      severity,
                "server_id":     server_id,
            })
            return cursor.lastrowid
    except Exception as exc:
        logger.exception("insert_anomaly failed: %s", exc)
        raise


def get_anomalies(limit: int = 50, server_id: int = None) -> list[dict]:
    """Return the most recent *limit* anomaly records for a specific server."""
    try:
        if not server_id:
            sql = "SELECT * FROM anomalies WHERE server_id IS NULL OR server_id = 0 ORDER BY id DESC LIMIT ?"
            params = (limit,)
        else:
            sql = "SELECT * FROM anomalies WHERE server_id = ? ORDER BY id DESC LIMIT ?"
            params = (server_id, limit)
            
        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.exception("get_anomalies failed: %s", exc)
        return []


# ─── Alerts ───────────────────────────────────────────────────────────────────

def insert_alert(message: str, status: str = "pending") -> int:
    """Log a new alert entry."""
    sql = """
    INSERT INTO alerts (timestamp, message, status)
    VALUES (datetime('now'), :message, :status)
    """
    try:
        with get_connection() as conn:
            cursor = conn.execute(sql, {"message": message, "status": status})
            return cursor.lastrowid
    except Exception as exc:
        logger.exception("insert_alert failed: %s", exc)
        raise


def update_alert_status(alert_id: int, status: str) -> None:
    """Update the delivery status of an existing alert."""
    sql = "UPDATE alerts SET status = ? WHERE id = ?"
    try:
        with get_connection() as conn:
            conn.execute(sql, (status, alert_id))
    except Exception as exc:
        logger.exception("update_alert_status failed: %s", exc)


def get_alerts(limit: int = 50) -> list[dict]:
    """Return the most recent *limit* alert records."""
    sql = "SELECT * FROM alerts ORDER BY id DESC LIMIT ?"
    try:
        with get_connection() as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
            return [dict(r) for r in rows]
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
