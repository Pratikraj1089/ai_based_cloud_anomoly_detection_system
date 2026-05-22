"""
============================================================
config.py — Central Configuration File
Project : AI-Based Anomaly Detection System for Cloud Resource Monitoring
Org     : NTPL Digital Private Limited, Noida
Group   : CU - MCA - Group-4
------------------------------------------------------------
All project-wide settings are defined here.
No hardcoded values should exist in any other file.
============================================================
"""

import os
import socket

def _get_default_hostname():
    try:
        return socket.gethostname()
    except Exception:
        return "VPS-Server-01"

def _get_default_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ─── Agent / Server Identity ────────────────────────────────────────────────
_env_name = os.getenv("SERVER_NAME", "")
SERVER_NAME = _env_name if (_env_name and _env_name != "VPS-Server-01") else _get_default_hostname()

_env_ip = os.getenv("SERVER_IP", "")
SERVER_IP = _env_ip if (_env_ip and _env_ip != "127.0.0.1") else _get_default_ip()

# ─── API Settings ────────────────────────────────────────────────────────────
API_HOST      = os.getenv("API_HOST", "0.0.0.0")
API_PORT      = int(os.getenv("API_PORT", 5000))
API_BASE_URL  = os.getenv("API_BASE_URL", f"http://127.0.0.1:{API_PORT}")
API_KEY       = os.getenv("API_KEY", "mca-group4-secret-key")   # shared secret between agent & API

# ─── Agent Settings ──────────────────────────────────────────────────────────
AGENT_INTERVAL_SECONDS = int(os.getenv("AGENT_INTERVAL", 30))   # collect & send every N seconds
AGENT_MAX_RETRIES      = int(os.getenv("AGENT_MAX_RETRIES", 5))
AGENT_RETRY_DELAY      = int(os.getenv("AGENT_RETRY_DELAY", 10)) # seconds between retries

# ─── Database ────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.getenv("DB_PATH", os.path.join(BASE_DIR, "backend", "anomaly_detection.db"))

# ─── ML Model ────────────────────────────────────────────────────────────────
MODEL_PATH          = os.getenv("MODEL_PATH", os.path.join(BASE_DIR, "backend", "isolation_forest.joblib"))
SCALER_PATH         = os.getenv("SCALER_PATH", os.path.join(BASE_DIR, "backend", "scaler.joblib"))
MODEL_TRAIN_SAMPLES = int(os.getenv("MODEL_TRAIN_SAMPLES", 200))   # train after this many readings
CONTAMINATION       = float(os.getenv("CONTAMINATION", 0.05))       # Isolation Forest contamination

# ─── Anomaly Score Thresholds ────────────────────────────────────────────────
THRESHOLD_LOW    = -0.1   # score < this → Low anomaly
THRESHOLD_MEDIUM = -0.3   # score < this → Medium anomaly
THRESHOLD_HIGH   = -0.5   # score < this → High anomaly

# ─── Dashboard ───────────────────────────────────────────────────────────────
DASHBOARD_POLL_INTERVAL_MS = int(os.getenv("DASHBOARD_POLL_MS", 10000))  # 10 s
MAX_CHART_POINTS           = int(os.getenv("MAX_CHART_POINTS", 100))

# ─── Alerts ──────────────────────────────────────────────────────────────────
ALERT_EMAIL_ENABLED  = os.getenv("ALERT_EMAIL_ENABLED", "false").lower() == "true"
ALERT_EMAIL_FROM     = os.getenv("ALERT_EMAIL_FROM", "alerts@yourcompany.com")
ALERT_EMAIL_TO       = os.getenv("ALERT_EMAIL_TO", "admin@yourcompany.com")
SMTP_HOST            = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT            = int(os.getenv("SMTP_PORT", 587))
SMTP_USER            = os.getenv("SMTP_USER", "")
SMTP_PASSWORD        = os.getenv("SMTP_PASSWORD", "")

ALERT_WEBHOOK_ENABLED = os.getenv("ALERT_WEBHOOK_ENABLED", "false").lower() == "true"
ALERT_WEBHOOK_URL     = os.getenv("ALERT_WEBHOOK_URL", "")

# ─── Logging ─────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR   = os.getenv("LOG_DIR", os.path.join(BASE_DIR, "logs"))
