"""
============================================================
model.py — AI Anomaly Detection Logic (Isolation Forest)
Project : AI-Based Anomaly Detection System for Cloud Resource Monitoring
Org     : NTPL Digital Private Limited, Noida
Group   : CU - MCA - Group-4
------------------------------------------------------------
Responsibilities:
  - Train an Isolation Forest model on collected metrics
  - Persist model & scaler to disk with joblib
  - Predict anomaly / normal for every new reading
  - Return anomaly score and severity label
============================================================
"""

import os
import sys
import logging
import numpy as np

from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import joblib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    MODEL_PATH, SCALER_PATH,
    CONTAMINATION, MODEL_TRAIN_SAMPLES,
    THRESHOLD_LOW, THRESHOLD_MEDIUM, THRESHOLD_HIGH,
)
from backend.hybrid_pipeline import preprocess_features, classify_vps_situation

logger = logging.getLogger(__name__)

# Feature columns used for training / prediction after engineering
FEATURE_COLS = ["cpu_distance_to_danger", "ram_distance_to_danger", "disk", "network_in", "network_out", "processes"]


# ─── Utility: extract feature vector from a metric dict ──────────────────────

def _to_feature_vector(metric: dict) -> list[float]:
    """Convert a metric dictionary to a preprocessed fixed-order feature list."""
    processed = preprocess_features(metric)
    return [float(processed.get(col, 0)) for col in FEATURE_COLS]


# ─── Model State ─────────────────────────────────────────────────────────────

class AnomalyDetector:
    """
    Wraps sklearn's IsolationForest with:
      - StandardScaler for normalisation
      - Persistent save / load via joblib
      - Simple severity classification based on anomaly score
    """

    def __init__(self, server_id: int = 0):
        self.server_id = server_id
        self.model_path = MODEL_PATH.replace(".joblib", f"_{server_id}.joblib")
        self.scaler_path = SCALER_PATH.replace(".joblib", f"_{server_id}.joblib")
        self.model: IsolationForest | None = None
        self.scaler: StandardScaler | None = None
        self.is_trained: bool = False
        self._try_load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _try_load(self) -> None:
        """Attempt to load a previously saved model from disk."""
        if os.path.exists(self.model_path) and os.path.exists(self.scaler_path):
            try:
                self.model  = joblib.load(self.model_path)
                self.scaler = joblib.load(self.scaler_path)
                self.is_trained = True
                logger.info("Loaded existing model from %s", self.model_path)
            except Exception as exc:
                logger.warning("Could not load saved model (%s). Will retrain.", exc)

    def _save(self) -> None:
        """Persist the current model and scaler to disk."""
        try:
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            joblib.dump(self.model,  self.model_path)
            joblib.dump(self.scaler, self.scaler_path)
            logger.info("Model saved to %s", self.model_path)
        except Exception as exc:
            logger.error("Failed to save model: %s", exc)

    # ── Training ─────────────────────────────────────────────────────────────

    def train(self, metrics: list[dict]) -> bool:
        """
        Train the Isolation Forest on a list of metric dicts.
        Requires at least MODEL_TRAIN_SAMPLES rows.
        Returns True on success.
        """
        if len(metrics) < MODEL_TRAIN_SAMPLES:
            logger.warning(
                "Not enough data to train. Need %d, have %d.",
                MODEL_TRAIN_SAMPLES, len(metrics)
            )
            return False

        try:
            X = np.array([_to_feature_vector(m) for m in metrics])

            # Fit scaler
            self.scaler = StandardScaler()
            X_scaled = self.scaler.fit_transform(X)

            # Fit Isolation Forest
            self.model = IsolationForest(
                n_estimators=200,
                contamination=CONTAMINATION,
                random_state=42,
                n_jobs=-1,
            )
            self.model.fit(X_scaled)
            self.is_trained = True
            self._save()

            logger.info(
                "Model trained on %d samples (contamination=%.2f).",
                len(metrics), CONTAMINATION
            )
            return True

        except Exception as exc:
            logger.exception("Training failed: %s", exc)
            return False

    # ── Prediction ───────────────────────────────────────────────────────────

    def predict(self, metric: dict) -> dict:
        """
        Predict whether a single metric reading is an anomaly.

        Returns a dict:
          {
            "is_anomaly": bool,
            "score":      float,   # raw anomaly score (negative = anomaly)
            "severity":   str,     # "Normal" | "Moderate Anomaly" | "High Anomaly" | "Danger"
            "trained":    bool,    # whether the model has been trained
          }
        """
        if not self.is_trained:
            # Fallback to pure rule-based classification if model is not trained yet
            # In this case, we pass ai_score = 0.0 (safe)
            cpu = float(metric.get("cpu", 0.0))
            ram = float(metric.get("ram", 0.0))
            cpu_cores = int(metric.get("cpu_cores", 1))
            load_avg_1m = float(metric.get("load_avg_1m", (cpu * cpu_cores / 100.0)))
            severity = classify_vps_situation(
                cpu=cpu,
                ram=ram,
                load_avg_1m=load_avg_1m,
                cpu_cores=cpu_cores,
                ai_score=0.0
            )
            return {
                "is_anomaly": severity != "Normal",
                "score":      0.0,
                "severity":   severity,
                "trained":    False,
            }

        try:
            x       = np.array([_to_feature_vector(metric)])
            x_sc    = self.scaler.transform(x)
            score   = float(self.model.score_samples(x_sc)[0])
            
            cpu = float(metric.get("cpu", 0.0))
            ram = float(metric.get("ram", 0.0))
            cpu_cores = int(metric.get("cpu_cores", 1))
            load_avg_1m = float(metric.get("load_avg_1m", (cpu * cpu_cores / 100.0)))
            
            severity = classify_vps_situation(
                cpu=cpu,
                ram=ram,
                load_avg_1m=load_avg_1m,
                cpu_cores=cpu_cores,
                ai_score=score
            )

            return {
                "is_anomaly": severity != "Normal",
                "score":      round(score, 4),
                "severity":   severity,
                "trained":    True,
            }

        except Exception as exc:
            logger.exception("Prediction failed: %s", exc)
            return {
                "is_anomaly": False,
                "score":      0.0,
                "severity":   "Normal",
                "trained":    True,
            }

    # ── Model Status ─────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Return a summary of the current model state."""
        return {
            "is_trained":    self.is_trained,
            "model_path":    self.model_path if self.is_trained else None,
            "contamination": CONTAMINATION,
            "features":      FEATURE_COLS,
            "train_samples_required": MODEL_TRAIN_SAMPLES,
            "thresholds": {
                "ai_fallback_threshold": -0.75
            },
        }


# ─── Module-level registry ───────────────────────────────────────────────────
_detectors = {}

def get_detector(server_id: int = 0) -> AnomalyDetector:
    """Get or create the AnomalyDetector instance for a specific server."""
    server_id = int(server_id or 0)
    if server_id not in _detectors:
        _detectors[server_id] = AnomalyDetector(server_id)
    return _detectors[server_id]
