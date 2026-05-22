"""
================================================================================
hybrid_pipeline.py — Hybrid Anomaly Detection Pipeline
Project : AI-Based Anomaly Detection System for Cloud Resource Monitoring
Org     : NTPL Digital Private Limited, Noida
Group   : CU - MCA - Group-4
--------------------------------------------------------------------------------
This module implements a hybrid anomaly detection pipeline combining:
  1. Data Preprocessing & Feature Engineering (Relative Distance Metrics)
  2. A Rule-Based metrics gate for multi-class severity mapping (classify_vps_situation)
================================================================================
"""

import sys
from typing import Dict, Any

# ─── Feature Engineering Explanation ──────────────────────────────────────────
# Unsupervised algorithms (such as Isolation Forest) isolate outliers by randomly
# partitioning feature dimensions. When training on raw CPU/RAM (0% to 100%),
# normal idle behaviors (e.g. CPU minor jumps between 2% and 15%) can sometimes
# appear statistically sparse and cause false positive alerts.
#
# By transforming raw utilization into "Distance to Danger" metrics:
#   CPU_Distance_To_Danger = 100.0 - Current_CPU
#   RAM_Distance_To_Danger = 100.0 - Current_RAM
#
# Safe, idle states result in large values (close to 100) that form dense clusters.
# In an Isolation Forest, these dense clusters require many partitions (splits)
# to isolate, resulting in highly "normal" anomaly scores (close to 1.0).
# Conversely, dangerous resource consumption states approach 0, becoming highly
# isolated in the feature space and flagging them as statistical anomalies,
# while ignoring minor low-end fluctuations.


def preprocess_features(metric: Dict[str, Any]) -> Dict[str, Any]:
    """
    Preprocess raw metrics by calculating relative distance to danger metrics.
    
    Args:
        metric: Dictionary containing raw 'cpu' and 'ram' percentage keys.
        
    Returns:
        A new dictionary with 'cpu_distance_to_danger' and 'ram_distance_to_danger'.
    """
    cpu = float(metric.get("cpu", 0.0))
    ram = float(metric.get("ram", 0.0))
    
    processed = metric.copy()
    processed["cpu_distance_to_danger"] = max(0.0, 100.0 - cpu)
    processed["ram_distance_to_danger"] = max(0.0, 100.0 - ram)
    return processed


def classify_vps_situation(
    cpu: float,
    ram: float,
    load_avg_1m: float,
    cpu_cores: int,
    ai_score: float
) -> str:
    """
    Determine the final system state based on hardware metrics, load ratio,
    and the AI model's continuous anomaly score.
    
    Args:
        cpu: Raw CPU utilization percentage (0.0 to 100.0).
        ram: Raw RAM utilization percentage (0.0 to 100.0).
        load_avg_1m: 1-minute system load average.
        cpu_cores: Number of CPU cores available on the system.
        ai_score: Continuous anomaly score from Isolation Forest (-1.0 to 1.0).
        
    Returns:
        Final state: 'Danger', 'High Anomaly', 'Moderate Anomaly', or 'Normal'.
    """
    # 1. Calculate Load Ratio
    load_ratio = load_avg_1m / cpu_cores if cpu_cores > 0 else 0.0
    
    # 2. Danger / Critical State Check
    if cpu >= 95.0 or load_ratio >= 1.0 or ram >= 95.0:
        return "Danger"
        
    # 3. High Anomaly State Check
    if cpu >= 85.0 or load_ratio >= 0.85 or ram >= 90.0:
        return "High Anomaly"
        
    # 4. Moderate Anomaly State Check
    if cpu >= 70.0 or load_ratio >= 0.70 or ram >= 80.0:
        return "Moderate Anomaly"
        
    # 5. AI Anomaly Fallback (The Sneaky Anomaly Catcher)
    # Detects situations like sudden service crashes (e.g. CPU dropping to 0%
    # or unusual process counts) while hardware utilization is safe.
    if ai_score <= -0.75:
        return "Moderate Anomaly"
        
    # 6. Normal State
    return "Normal"


# ─── Mock Data Test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("======================================================================")
    print("Running Mock Data Test for Hybrid Anomaly Pipeline")
    print("======================================================================\n")
    
    # Mock CPU cores
    cores = 4
    
    # Test cases: (name, cpu, ram, load_1m, ai_score)
    test_cases = [
        ("Idle Server (Normal)", 4.0, 35.0, 0.2, 0.45),
        ("Server in Danger State (High CPU)", 96.0, 60.0, 2.5, -0.65),
        ("Server in Danger State (High Load)", 45.0, 50.0, 4.2, -0.40),
        ("Server in Danger State (High RAM)", 50.0, 97.0, 1.1, -0.55),
        ("High Anomaly State (CPU Spike)", 88.0, 75.0, 3.1, -0.70),
        ("Moderate Anomaly State (RAM Leak)", 60.0, 82.0, 1.5, -0.60),
        ("Sneaky AI Anomaly (Hardware Safe, but AI detected crash)", 0.0, 35.0, 0.05, -0.82),
    ]
    
    for name, cpu, ram, load, ai_score in test_cases:
        # Show feature preprocessing
        raw_metrics = {"cpu": cpu, "ram": ram}
        preprocessed = preprocess_features(raw_metrics)
        
        # Get hybrid classification
        state = classify_vps_situation(
            cpu=cpu,
            ram=ram,
            load_avg_1m=load,
            cpu_cores=cores,
            ai_score=ai_score
        )
        
        print(f"Scenario: {name}")
        print(f"  -> Raw Input: CPU={cpu}%, RAM={ram}%, Load={load} (Cores={cores}), AI Score={ai_score}")
        print(f"  -> Engineered Features: CPU_Dist={preprocessed['cpu_distance_to_danger']}, RAM_Dist={preprocessed['ram_distance_to_danger']}")
        print(f"  -> Hybrid Pipeline Result: **{state}**")
        print("-" * 70)
