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
from config import (
    ALERT_EMAIL_ENABLED, ALERT_EMAIL_FROM, ALERT_EMAIL_TO,
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
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

    if ALERT_EMAIL_ENABLED:
        ok = _send_email(message, severity)
        if not ok:
            success = False

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


# ─── Email Channel ───────────────────────────────────────────────────────────

def _send_email(message: str, severity: str) -> bool:
    """
    Send an alert email via SMTP.
    Returns True on success, False on failure.
    """
    try:
        subject = f"🚨 Cloud Anomaly Alert — {severity.upper()} | {SERVER_NAME}"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = ALERT_EMAIL_FROM
        msg["To"]      = ALERT_EMAIL_TO

        # Plain text part
        msg.attach(MIMEText(message, "plain"))

        # HTML part for nicer rendering
        html_body = _build_html_email(message, severity)
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(ALERT_EMAIL_FROM, ALERT_EMAIL_TO, msg.as_string())

        logger.info("Alert email sent to %s", ALERT_EMAIL_TO)
        return True

    except Exception as exc:
        logger.error("Email alert failed: %s", exc)
        return False


def _build_html_email(plain_text: str, severity: str) -> str:
    """Create a minimal HTML version of the alert email."""
    color = {"low": "#f0ad4e", "medium": "#d9534f", "high": "#c0392b"}.get(severity, "#555")
    lines = plain_text.replace("\n", "<br>")
    return f"""
    <html><body style="font-family:monospace;background:#1a1a2e;color:#e0e0e0;padding:20px;">
      <h2 style="color:{color};">🚨 Anomaly Detected — {severity.upper()}</h2>
      <pre style="background:#16213e;padding:15px;border-radius:8px;border-left:4px solid {color};">
{lines}
      </pre>
      <p style="color:#888;font-size:12px;">
        NTPL Digital Pvt Ltd — AI Anomaly Detection System (CU MCA Group-4)
      </p>
    </body></html>
    """


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
