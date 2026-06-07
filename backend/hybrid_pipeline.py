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
    IFOREST_MODERATE_THRESHOLD, IFOREST_HIGH_THRESHOLD, IFOREST_DANGER_THRESHOLD,
    NET_MIN_SAFE_BASELINE, NET_SPIKE_MODERATE, NET_SPIKE_HIGH, NET_SPIKE_DANGER,
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
    network_in: float = 0.0,
    network_out: float = 0.0,
    net_in_5min_avg: float = 0.0,
    net_out_5min_avg: float = 0.0,
) -> str:
    """
    Determine the final system state using a hybrid approach:
      - Hard static thresholds (CPU, RAM, Load) are checked first.
      - Network anomalies are checked via baseline ratios, falling back to absolute MB/s limits.
      - AI anomaly score (Isolation Forest) mapping provides severity-aware fallback levels.

    Args:
        cpu              : CPU utilisation % (0–100)
        ram              : RAM utilisation % (0–100)
        load_avg_1m      : 1-minute system load average
        cpu_cores        : number of logical CPU cores
        ai_score         : Isolation Forest score_samples() value
        network_in       : Current inbound network rate (B/s)
        network_out      : Current outbound network rate (B/s)
        net_in_5min_avg  : 5-minute rolling average of inbound network rate (B/s)
        net_out_5min_avg : 5-minute rolling average of outbound network rate (B/s)

    Returns:
        One of: "Normal", "Moderate Anomaly", "High Anomaly", "Danger"
    """
    load_ratio = load_avg_1m / cpu_cores if cpu_cores > 0 else 0.0

    # 1. Determine severity based on CPU, RAM, and System Load
    static_severity = "Normal"
    if cpu >= CPU_DANGER or load_ratio >= LOAD_DANGER or ram >= RAM_DANGER:
        static_severity = "Danger"
    elif cpu >= CPU_HIGH or load_ratio >= LOAD_HIGH or ram >= RAM_HIGH:
        static_severity = "High Anomaly"
    elif cpu >= CPU_MODERATE or load_ratio >= LOAD_MODERATE or ram >= RAM_MODERATE:
        static_severity = "Moderate Anomaly"

    # 2. Determine severity based on Isolation Forest AI Score
    ai_severity = "Normal"
    if ai_score <= IFOREST_DANGER_THRESHOLD:
        ai_severity = "Danger"
    elif ai_score <= IFOREST_HIGH_THRESHOLD:
        ai_severity = "High Anomaly"
    elif ai_score <= IFOREST_MODERATE_THRESHOLD:
        ai_severity = "Moderate Anomaly"

    # 3. Determine severity based on Network Inbound Traffic
    in_ratio = network_in / max(net_in_5min_avg, NET_MIN_SAFE_BASELINE)
    in_severity = "Normal"
    if in_ratio >= 8.0:
        in_severity = "Danger"
    elif in_ratio >= 4.0:
        in_severity = "High Anomaly"
    elif in_ratio >= 2.0:
        in_severity = "Moderate Anomaly"

    # Absolute fallback check for Inbound
    if in_severity == "Normal":
        if network_in >= NET_SPIKE_DANGER:
            in_severity = "Danger"
        elif network_in >= NET_SPIKE_HIGH:
            in_severity = "High Anomaly"
        elif network_in >= NET_SPIKE_MODERATE:
            in_severity = "Moderate Anomaly"

    # 4. Determine severity based on Network Outbound Traffic
    out_ratio = network_out / max(net_out_5min_avg, NET_MIN_SAFE_BASELINE)
    out_severity = "Normal"
    if out_ratio >= 8.0:
        out_severity = "Danger"
    elif out_ratio >= 4.0:
        out_severity = "High Anomaly"
    elif out_ratio >= 2.0:
        out_severity = "Moderate Anomaly"

    # Absolute fallback check for Outbound
    if out_severity == "Normal":
        if network_out >= NET_SPIKE_DANGER:
            out_severity = "Danger"
        elif network_out >= NET_SPIKE_HIGH:
            out_severity = "High Anomaly"
        elif network_out >= NET_SPIKE_MODERATE:
            out_severity = "Moderate Anomaly"

    # Combine all severities and return the maximum active severity
    severity_order = {"Normal": 0, "Moderate Anomaly": 1, "High Anomaly": 2, "Danger": 3}
    final_severity = "Normal"
    for sev in (static_severity, ai_severity, in_severity, out_severity):
        if severity_order[sev] > severity_order[final_severity]:
            final_severity = sev

    return final_severity
