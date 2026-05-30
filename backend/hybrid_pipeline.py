"""
================================================================================
hybrid_pipeline.py — Feature Engineering + Hybrid Classification (AIOps Edition)
Project : Smart Cloud Pulse AI Monitor
Org     : NTPL Digital Private Limited, Noida
Group   : CU - MCA - Group-4
--------------------------------------------------------------------------------
This module does two things:

1. preprocess_features(metric, trend)
   Converts raw metric values + trend delta/averages into a richer feature
   vector that the Isolation Forest model can learn from more effectively.

   Core technique — "Distance to Danger":
     cpu_distance_to_danger = 100 - cpu
     ram_distance_to_danger = 100 - ram
   Safe idle states cluster at high values (easy for Isolation Forest to
   confirm as normal). Dangerous states approach 0 (become isolated outliers).

   Trend features add temporal awareness:
   - Delta features: catch sudden spikes/drops even when absolute levels are OK.
   - Rolling averages: provide a stable baseline context.

2. classify_vps_situation(cpu, ram, load_avg_1m, cpu_cores, ai_score)
   A 4-level severity classifier that combines hard thresholds with the AI
   score. Thresholds are fully configurable via .env.
================================================================================
"""

import sys
import os
from typing import Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    CPU_MODERATE, CPU_HIGH, CPU_DANGER,
    RAM_MODERATE, RAM_HIGH, RAM_DANGER,
    LOAD_MODERATE, LOAD_HIGH, LOAD_DANGER,
)


# ─── Feature Preprocessing ───────────────────────────────────────────────────

def preprocess_features(metric: Dict[str, Any],
                         trend: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Build the full feature dict used by the Isolation Forest.

    Args:
        metric : raw metric snapshot (cpu, ram, disk, network_in, …)
        trend  : output of trend_engine.get_trend_features() — optional.
                 When None (first reading, no history yet), trend features
                 are all set to 0.

    Returns:
        Processed dict containing both engineered and trend features.
    """
    if trend is None:
        trend = {}

    cpu = float(metric.get("cpu", 0.0))
    ram = float(metric.get("ram", 0.0))

    processed = metric.copy()

    # ── Distance-to-danger transformation ────────────────────────────────────
    # Idle states → high values (dense cluster → model says "normal")
    # High utilisation → near zero (isolated point → model says "anomaly")
    processed["cpu_distance_to_danger"] = max(0.0, 100.0 - cpu)
    processed["ram_distance_to_danger"] = max(0.0, 100.0 - ram)

    # ── Merge trend features (default 0 when unavailable) ────────────────────
    processed["cpu_delta"]       = trend.get("cpu_delta",       0.0)
    processed["ram_delta"]       = trend.get("ram_delta",       0.0)
    processed["disk_delta"]      = trend.get("disk_delta",      0.0)
    processed["process_delta"]   = trend.get("process_delta",   0.0)
    processed["net_in_delta"]    = trend.get("net_in_delta",    0.0)
    processed["net_out_delta"]   = trend.get("net_out_delta",   0.0)
    processed["cpu_5min_avg"]    = trend.get("cpu_5min_avg",    cpu)
    processed["ram_5min_avg"]    = trend.get("ram_5min_avg",    ram)
    processed["net_in_5min_avg"] = trend.get("net_in_5min_avg", float(metric.get("network_in",  0)))
    processed["proc_5min_avg"]   = trend.get("proc_5min_avg",   float(metric.get("processes",   0)))

    return processed


# ─── Severity Classifier ─────────────────────────────────────────────────────

def classify_vps_situation(
    cpu: float,
    ram: float,
    load_avg_1m: float,
    cpu_cores: int,
    ai_score: float,
) -> str:
    """
    Determine the final system state using a hybrid approach:
      - Hard thresholds (configurable via .env) are checked first
      - AI anomaly score acts as a fallback "sneaky anomaly" detector

    The AI fallback catches situations where hardware metrics look fine
    but the overall statistical pattern is unusual (e.g. service crash
    causing both CPU and process count to drop simultaneously).

    Args:
        cpu         : CPU utilisation % (0–100)
        ram         : RAM utilisation % (0–100)
        load_avg_1m : 1-minute system load average
        cpu_cores   : number of logical CPU cores
        ai_score    : Isolation Forest score_samples() value

    Returns:
        One of: "Normal", "Moderate Anomaly", "High Anomaly", "Danger"
    """
    load_ratio = load_avg_1m / cpu_cores if cpu_cores > 0 else 0.0

    # Danger / Critical
    if cpu >= CPU_DANGER or load_ratio >= LOAD_DANGER or ram >= RAM_DANGER:
        return "Danger"

    # High Anomaly
    if cpu >= CPU_HIGH or load_ratio >= LOAD_HIGH or ram >= RAM_HIGH:
        return "High Anomaly"

    # Moderate Anomaly
    if cpu >= CPU_MODERATE or load_ratio >= LOAD_MODERATE or ram >= RAM_MODERATE:
        return "Moderate Anomaly"

    # AI Anomaly Fallback — catches "sneaky" anomalies:
    # e.g. CPU at 5% but process count collapsed, which the IF model flags
    if ai_score <= -0.75:
        return "Moderate Anomaly"

    return "Normal"
