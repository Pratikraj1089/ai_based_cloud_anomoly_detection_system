"""
============================================================
monitor.py — VPS Agent (Metric Collector)
Project : AI-Based Anomaly Detection System for Cloud Resource Monitoring
Org     : NTPL Digital Private Limited, Noida
Group   : CU - MCA - Group-4
------------------------------------------------------------
This script runs on the monitored VPS.
Every AGENT_INTERVAL_SECONDS seconds it:
  1. Collects system metrics via psutil
  2. Serialises them to JSON
  3. POSTs them to the Flask API with an API key header
  4. Retries on failure up to AGENT_MAX_RETRIES times
------------------------------------------------------------
Usage:
  python agent/monitor.py             (foreground)
  nohup python agent/monitor.py &     (background)
============================================================
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timezone

import psutil
import requests
import schedule

# Allow importing config from project root
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from config import (
    API_BASE_URL, API_KEY,
    AGENT_INTERVAL_SECONDS, AGENT_MAX_RETRIES, AGENT_RETRY_DELAY,
    SERVER_NAME, SERVER_IP, LOG_DIR, LOG_LEVEL,
)

# ─── Logging ─────────────────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level  = getattr(logging, LOG_LEVEL, logging.INFO),
    format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, "agent.log")),
    ],
)
logger = logging.getLogger("monitor")

# ─── Network Baseline ────────────────────────────────────────────────────────
# We snapshot counters at startup to calculate per-interval byte rates
_prev_net_io    = psutil.net_io_counters()
_prev_net_time  = time.time()


# ─── Metric Collection ────────────────────────────────────────────────────────

def collect_metrics() -> dict:
    """
    Collect current system metrics using psutil.

    Returns a dict with keys:
      timestamp, cpu, ram, disk, network_in, network_out, processes, uptime
    """
    global _prev_net_io, _prev_net_time

    # CPU (non-blocking, 1-second interval)
    cpu_pct = psutil.cpu_percent(interval=1)

    # RAM
    mem     = psutil.virtual_memory()
    ram_pct = mem.percent

    # Disk (root partition)
    disk    = psutil.disk_usage("/")
    disk_pct = disk.percent

    # Network — bytes per second since last sample
    net_io   = psutil.net_io_counters()
    now      = time.time()
    elapsed  = now - _prev_net_time if (now - _prev_net_time) > 0 else 1.0

    net_in_rate  = (net_io.bytes_recv - _prev_net_io.bytes_recv) / elapsed
    net_out_rate = (net_io.bytes_sent - _prev_net_io.bytes_sent) / elapsed

    _prev_net_io   = net_io
    _prev_net_time = now

    # Number of running processes
    proc_count = len(psutil.pids())

    # System uptime in seconds
    boot_time  = psutil.boot_time()
    uptime_sec = time.time() - boot_time

    return {
        "timestamp":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "cpu":         round(cpu_pct,        2),
        "ram":         round(ram_pct,        2),
        "disk":        round(disk_pct,       2),
        "network_in":  round(net_in_rate,    2),
        "network_out": round(net_out_rate,   2),
        "processes":   proc_count,
        "uptime":      round(uptime_sec,     1),
        "server_name": SERVER_NAME,
        "server_ip":   SERVER_IP,
    }


# ─── API Sender ───────────────────────────────────────────────────────────────

def send_metrics(metrics: dict) -> bool:
    """
    POST metric data to the Flask API.
    Retries up to AGENT_MAX_RETRIES times with AGENT_RETRY_DELAY seconds delay.

    Returns True on success, False if all retries are exhausted.
    """
    url     = f"{API_BASE_URL}/api/metrics"
    headers = {
        "Content-Type": "application/json",
        "X-API-Key":    API_KEY,
    }

    for attempt in range(1, AGENT_MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=metrics, headers=headers, timeout=10)
            if resp.status_code == 201:
                result = resp.json()
                pred   = result.get("prediction", {})
                logger.info(
                    "Sent | CPU=%.1f%% RAM=%.1f%% Disk=%.1f%% | %s (score=%.4f)",
                    metrics["cpu"], metrics["ram"], metrics["disk"],
                    pred.get("severity", "?"), pred.get("score", 0),
                )
                return True
            else:
                logger.warning(
                    "API returned %d on attempt %d/%d: %s",
                    resp.status_code, attempt, AGENT_MAX_RETRIES, resp.text[:200],
                )

        except requests.exceptions.ConnectionError:
            logger.warning(
                "Connection refused (attempt %d/%d). Is the API running at %s?",
                attempt, AGENT_MAX_RETRIES, API_BASE_URL,
            )
        except requests.exceptions.Timeout:
            logger.warning("Request timed out (attempt %d/%d).", attempt, AGENT_MAX_RETRIES)
        except requests.exceptions.RequestException as exc:
            logger.error("Request error (attempt %d/%d): %s", attempt, AGENT_MAX_RETRIES, exc)

        if attempt < AGENT_MAX_RETRIES:
            logger.info("Retrying in %d seconds …", AGENT_RETRY_DELAY)
            time.sleep(AGENT_RETRY_DELAY)

    logger.error("All %d send attempts failed. Metric dropped.", AGENT_MAX_RETRIES)
    return False


# ─── Main Job ────────────────────────────────────────────────────────────────

def run_once() -> None:
    """Collect and send a single metric snapshot."""
    try:
        metrics = collect_metrics()
        send_metrics(metrics)
    except Exception as exc:
        logger.exception("Unexpected error in run_once: %s", exc)


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info(
        "=== VPS Monitor Agent starting — Server: %s (%s) ===",
        SERVER_NAME, SERVER_IP,
    )
    logger.info(
        "Sending metrics to %s every %d seconds.", API_BASE_URL, AGENT_INTERVAL_SECONDS
    )

    # Run once immediately so we don't wait for the first interval
    run_once()

    # Schedule recurring collection
    schedule.every(AGENT_INTERVAL_SECONDS).seconds.do(run_once)

    logger.info("Scheduler started. Press Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(1)
