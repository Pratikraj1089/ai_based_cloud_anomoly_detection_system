"""
============================================================
alerts.py — Alert / Notification System
Project : AI-Based Anomaly Detection System for Cloud Resource Monitoring
Org     : NTPL Digital Private Limited, Noida
Group   : CU - MCA - Group-4
------------------------------------------------------------
Responsibilities:
  - Log alerts to the database
  - Send email alerts via SMTP (when configured)
  - Send webhook alerts via HTTP POST (when configured)
  - Handle delivery failures gracefully with retry
============================================================
"""

import os
import sys
import json
import logging
import smtplib
import threading
from email.mime.text          import MIMEText
from email.mime.multipart     import MIMEMultipart
from datetime                 import datetime

import requests as req  # 'requests' library

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from config import (
    SMTP_ENABLED, ALERT_SENDER_EMAIL, ALERT_RECEIVER_EMAIL,
    SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
    ALERT_WEBHOOK_ENABLED, ALERT_WEBHOOK_URL,
    SERVER_NAME, SERVER_IP,
)
from backend.database import insert_alert, update_alert_status

logger = logging.getLogger(__name__)


# ─── Public Entry Point ──────────────────────────────────────────────────────

def dispatch_alert(severity: str, score: float, metric: dict) -> None:
    """
    Build an alert message and dispatch via all configured channels.
    This runs asynchronously so it never blocks the API response.

    Args:
        severity : "low" | "medium" | "high"
        score    : raw anomaly score from the model
        metric   : the metric dict that triggered the alert
    """
    message = _build_message(severity, score, metric)

    # Persist in DB immediately (as 'pending')
    try:
        alert_id = insert_alert(message, status="pending")
    except Exception as exc:
        logger.error("Could not insert alert into DB: %s", exc)
        alert_id = None

    # Dispatch channels in a background thread to avoid blocking Flask
    thread = threading.Thread(
        target=_send_all,
        args=(alert_id, message, severity, score, metric),
        daemon=True,
    )
    thread.start()


# ─── Message Builder ─────────────────────────────────────────────────────────

def _build_message(severity: str, score: float, metric: dict) -> str:
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    sev_upper = severity.upper()
    return (
        f"[{sev_upper} ANOMALY] {SERVER_NAME} ({SERVER_IP})\n"
        f"Time     : {ts}\n"
        f"Score    : {score:.4f}\n"
        f"CPU      : {metric.get('cpu', 'N/A')}%\n"
        f"RAM      : {metric.get('ram', 'N/A')}%\n"
        f"Disk     : {metric.get('disk', 'N/A')}%\n"
        f"Net In   : {metric.get('network_in', 'N/A')} bytes/s\n"
        f"Net Out  : {metric.get('network_out', 'N/A')} bytes/s\n"
        f"Processes: {metric.get('processes', 'N/A')}\n"
        f"Severity : {sev_upper}"
    )


# ─── Dispatch Coordinator ────────────────────────────────────────────────────

def _send_all(alert_id, message: str, severity: str, score: float, metric: dict) -> None:
    """Run all channels; update DB status to 'sent' or 'failed'."""
    success = True

    # 1. SMTP Email Alerts
    if config.SMTP_ENABLED:
        try:
            from backend.email_alerts import send_alert_email
            from backend.trend_engine import get_trend_features
            from backend.explainer import explain
            from backend.root_cause import classify_root_cause
            
            server_id = metric.get("server_id", 0)
            trend = get_trend_features(server_id, metric)
            reasons = explain(metric, trend, {"score": score})
            consec_ram = trend.get("consecutive_ram_increases", 0)
            probable_cause = classify_root_cause(metric, trend, consec_ram)
            
            ok = send_alert_email(
                server_id=server_id,
                severity=severity,
                score=score,
                metric=metric,
                trend=trend,
                reasons=reasons,
                probable_cause=probable_cause
            )
            if not ok:
                success = False
        except Exception as exc:
            logger.exception("Email alert dispatch crashed: %s", exc)
            success = False

    # 2. Webhook Alerts
    if ALERT_WEBHOOK_ENABLED and ALERT_WEBHOOK_URL:
        ok = _send_webhook(severity, score, metric, message)
        if not ok:
            success = False

    # Always log to console
    _log_to_console(severity, message)

    if alert_id:
        status = "sent" if success else "failed"
        try:
            update_alert_status(alert_id, status)
        except Exception as exc:
            logger.error("Could not update alert status: %s", exc)


# ─── Webhook Channel ─────────────────────────────────────────────────────────

def _send_webhook(severity: str, score: float, metric: dict, message: str) -> bool:
    """
    POST alert data as JSON to the configured webhook URL (e.g. Slack, Teams, custom).
    Returns True on success, False on failure.
    """
    payload = {
        "server":    SERVER_NAME,
        "ip":        SERVER_IP,
        "severity":  severity,
        "score":     score,
        "metric":    metric,
        "message":   message,
        "timestamp": datetime.utcnow().isoformat(),
    }
    try:
        resp = req.post(
            ALERT_WEBHOOK_URL,
            json=payload,
            timeout=5,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        logger.info("Webhook alert sent (status %d).", resp.status_code)
        return True
    except Exception as exc:
        logger.error("Webhook alert failed: %s", exc)
        return False


# ─── Console Logger ──────────────────────────────────────────────────────────

def _log_to_console(severity: str, message: str) -> None:
    """Always emit anomaly alerts as WARNING/ERROR log lines."""
    if severity == "high":
        logger.error("HIGH ANOMALY ALERT:\n%s", message)
    elif severity == "medium":
        logger.warning("MEDIUM ANOMALY ALERT:\n%s", message)
    else:
        logger.warning("LOW ANOMALY ALERT:\n%s", message)
