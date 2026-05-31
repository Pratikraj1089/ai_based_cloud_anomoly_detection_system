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
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv(override=True)

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
MODEL_TRAIN_SAMPLES = int(os.getenv("MODEL_TRAIN_SAMPLES", 20))   # train after this many readings
CONTAMINATION       = float(os.getenv("CONTAMINATION", 0.05))       # Isolation Forest contamination

# ─── Anomaly Score Thresholds ────────────────────────────────────────────────
THRESHOLD_LOW    = -0.1   # score < this → Low anomaly
THRESHOLD_MEDIUM = -0.3   # score < this → Medium anomaly
THRESHOLD_HIGH   = -0.5   # score < this → High anomaly

# ─── Custom Hardware Thresholds ──────────────────────────────────────────────
CPU_MODERATE       = float(os.getenv("CPU_MODERATE", 70.0))
CPU_HIGH           = float(os.getenv("CPU_HIGH", 85.0))
CPU_DANGER         = float(os.getenv("CPU_DANGER", 95.0))

RAM_MODERATE       = float(os.getenv("RAM_MODERATE", 80.0))
RAM_HIGH           = float(os.getenv("RAM_HIGH", 90.0))
RAM_DANGER         = float(os.getenv("RAM_DANGER", 95.0))

DISK_MODERATE      = float(os.getenv("DISK_MODERATE", 70.0))
DISK_HIGH          = float(os.getenv("DISK_HIGH", 85.0))
DISK_DANGER        = float(os.getenv("DISK_DANGER", 95.0))

LOAD_MODERATE      = float(os.getenv("LOAD_MODERATE", 0.70))
LOAD_HIGH          = float(os.getenv("LOAD_HIGH", 0.85))
LOAD_DANGER        = float(os.getenv("LOAD_DANGER", 1.0))

NET_SPIKE_MODERATE = float(os.getenv("NET_SPIKE_MODERATE", 5000000.0))
NET_SPIKE_HIGH     = float(os.getenv("NET_SPIKE_HIGH", 20000000.0))
NET_SPIKE_DANGER   = float(os.getenv("NET_SPIKE_DANGER", 50000000.0))

PROCESS_DROP_MODERATE = float(os.getenv("PROCESS_DROP_MODERATE", 5.0))
PROCESS_DROP_HIGH     = float(os.getenv("PROCESS_DROP_HIGH", 15.0))
PROCESS_DROP_DANGER   = float(os.getenv("PROCESS_DROP_DANGER", 30.0))

# ─── Alert Suppressor Settings ───────────────────────────────────────────────
ALERT_CONSECUTIVE_MIN  = int(os.getenv("ALERT_CONSECUTIVE_MIN", 2))
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", 5))

# ─── Telegram Alerts Settings ────────────────────────────────────────────────
TELEGRAM_ENABLED   = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Auto-Remediation Settings ────────────────────────────────────────────────
REMEDIATION_ENABLED  = os.getenv("REMEDIATION_ENABLED", "false").lower() == "true"
REMEDIATION_SERVICES = os.getenv("REMEDIATION_SERVICES", "nginx,apache2,mysql,postgresql")

# ─── Dashboard ───────────────────────────────────────────────────────────────
DASHBOARD_POLL_INTERVAL_MS = int(os.getenv("DASHBOARD_POLL_MS", 10000))  # 10 s
MAX_CHART_POINTS           = int(os.getenv("MAX_CHART_POINTS", 100))

# ─── Alerts & SMTP Configuration ─────────────────────────────────────────────
SMTP_ENABLED         = os.getenv("SMTP_ENABLED", os.getenv("ALERT_EMAIL_ENABLED", "false")).lower() == "true"
SMTP_HOST            = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT            = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME        = os.getenv("SMTP_USERNAME", os.getenv("SMTP_USER", ""))
SMTP_PASSWORD        = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS         = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

ALERT_SENDER_EMAIL   = os.getenv("ALERT_SENDER_EMAIL", os.getenv("ALERT_EMAIL_FROM", "alerts@yourcompany.com"))
ALERT_RECEIVER_EMAIL = os.getenv("ALERT_RECEIVER_EMAIL", os.getenv("ALERT_EMAIL_TO", "admin@yourcompany.com"))

EMAIL_TIMEOUT_SECONDS         = int(os.getenv("EMAIL_TIMEOUT_SECONDS", 20))
EMAIL_SUBJECT_PREFIX          = os.getenv("EMAIL_SUBJECT_PREFIX", "[Smart Cloud Pulse]")
EMAIL_INCLUDE_RAW_METRICS     = os.getenv("EMAIL_INCLUDE_RAW_METRICS", "true").lower() == "true"
EMAIL_INCLUDE_AI_DETAILS      = os.getenv("EMAIL_INCLUDE_AI_DETAILS", "true").lower() == "true"
EMAIL_INCLUDE_RECOMMENDATIONS = os.getenv("EMAIL_INCLUDE_RECOMMENDATIONS", "true").lower() == "true"

ALERT_WEBHOOK_ENABLED = os.getenv("ALERT_WEBHOOK_ENABLED", "false").lower() == "true"
ALERT_WEBHOOK_URL     = os.getenv("ALERT_WEBHOOK_URL", "")

# ─── Logging ─────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR   = os.getenv("LOG_DIR", os.path.join(BASE_DIR, "logs"))
