"""
============================================================
model.py — Isolation Forest Anomaly Detector (AIOps Edition)
Project : Smart Cloud Pulse AI Monitor
Org     : NTPL Digital Private Limited, Noida
Group   : CU - MCA - Group-4
------------------------------------------------------------
Responsibilities:
  - Train an Isolation Forest on collected + trend-engineered metrics
  - Persist/load model & scaler via joblib
  - Predict anomaly score and severity for each new reading
  - Support per-server independent models via get_detector(server_id)
  - Gracefully handle feature-count mismatch (auto-retrain)
============================================================
"""

import os
import sys
import logging
import numpy as np

from sklearn.ensemble      import IsolationForest
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

# ─── Feature columns used by the model ───────────────────────────────────────
# Changing this list requires retraining all models. The _try_load() method
# automatically detects feature-count mismatches and discards stale models.
FEATURE_COLS = [
    # Core engineered features
    "cpu_distance_to_danger",   # 100 - cpu  (high = safe, low = danger)
    "ram_distance_to_danger",   # 100 - ram
    "disk",
    "network_in",
    "network_out",
    "processes",
    # Trend delta features (change since last poll)
    "cpu_delta",
    "ram_delta",
    "disk_delta",
    "process_delta",
    "net_in_delta",
    "net_out_delta",
    # Rolling averages (5-minute window)
    "cpu_5min_avg",
    "ram_5min_avg",
    "net_in_5min_avg",
    "proc_5min_avg",
]


# ─── Helper ──────────────────────────────────────────────────────────────────

def _to_feature_vector(metric: dict, trend: dict = None) -> list[float]:
    """Convert raw metric + trend into the fixed-order feature list."""
    processed = preprocess_features(metric, trend)
    return [float(processed.get(col, 0.0)) for col in FEATURE_COLS]


# ─── Detector Class ───────────────────────────────────────────────────────────

class AnomalyDetector:
    """
    Wraps sklearn IsolationForest with:
      - StandardScaler normalisation
      - Joblib-based model persistence
      - Automatic feature-count validation on load
      - Severity classification via hybrid pipeline
    """

    def __init__(self, server_id: int = 0):
        self.server_id   = int(server_id or 0)
        # Each server gets its own model files: isolation_forest_0.joblib etc.
        self.model_path  = MODEL_PATH.replace(".joblib",  f"_{self.server_id}.joblib")
        self.scaler_path = SCALER_PATH.replace(".joblib", f"_{self.server_id}.joblib")
        self.model:      IsolationForest | None = None
        self.scaler:     StandardScaler  | None = None
        self.is_trained: bool = False
        self._try_load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _try_load(self) -> None:
        """Load model from disk if it exists AND feature count still matches."""
        if not (os.path.exists(self.model_path) and os.path.exists(self.scaler_path)):
            return
        try:
            model  = joblib.load(self.model_path)
            scaler = joblib.load(self.scaler_path)

            # Guard: if the saved model was trained on a different feature set,
            # discard it so it will be retrained with the new feature columns.
            expected = len(FEATURE_COLS)
            if hasattr(model, "n_features_in_") and model.n_features_in_ != expected:
                logger.warning(
                    "Server %d: saved model has %d features but pipeline expects %d. "
                    "Discarding — will retrain automatically.",
                    self.server_id, model.n_features_in_, expected,
                )
                return

            self.model      = model
            self.scaler     = scaler
            self.is_trained = True
            logger.info("Server %d: loaded model from %s", self.server_id, self.model_path)
        except Exception as exc:
            logger.warning("Server %d: could not load model (%s). Will retrain.", self.server_id, exc)

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            joblib.dump(self.model,  self.model_path)
            joblib.dump(self.scaler, self.scaler_path)
            logger.info("Server %d: model saved to %s", self.server_id, self.model_path)
        except Exception as exc:
            logger.error("Server %d: failed to save model: %s", self.server_id, exc)

    # ── Training ─────────────────────────────────────────────────────────────

    def train(self, metrics: list[dict], trends: list[dict] = None) -> bool:
        """
        Train the Isolation Forest.

        Args:
            metrics : list of raw metric dicts (from database)
            trends  : corresponding trend feature dicts (optional; defaults to zeros)

        Returns True on success.
        """
        if len(metrics) < MODEL_TRAIN_SAMPLES:
            logger.warning(
                "Server %d: not enough data to train. Need %d, have %d.",
                self.server_id, MODEL_TRAIN_SAMPLES, len(metrics),
            )
            return False

        if trends is None:
            trends = [{}] * len(metrics)

        try:
            X = np.array([_to_feature_vector(m, t) for m, t in zip(metrics, trends)])

            self.scaler = StandardScaler()
            X_scaled    = self.scaler.fit_transform(X)

            self.model = IsolationForest(
                n_estimators  = 200,
                contamination = CONTAMINATION,
                random_state  = 42,
                n_jobs        = -1,
            )
            self.model.fit(X_scaled)
            self.is_trained = True
            self._save()

            logger.info(
                "Server %d: model trained on %d samples (%d features, contamination=%.2f).",
                self.server_id, len(metrics), len(FEATURE_COLS), CONTAMINATION,
            )
            return True

        except Exception as exc:
            logger.exception("Server %d: training failed: %s", self.server_id, exc)
            return False

    # ── Prediction ───────────────────────────────────────────────────────────

    def predict(self, metric: dict, trend: dict = None) -> dict:
        """
        Score a single metric reading.

        Returns:
            {
              "is_anomaly": bool,
              "score":      float,    # raw IF score (negative = more anomalous)
              "severity":   str,      # Normal | Moderate Anomaly | High Anomaly | Danger
              "trained":    bool,
            }
        """
        cpu        = float(metric.get("cpu",         0.0))
        ram        = float(metric.get("ram",         0.0))
        cpu_cores  = int(metric.get("cpu_cores",     1))
        load_avg   = float(metric.get("load_avg_1m", cpu * cpu_cores / 100.0))

        if not self.is_trained:
            # Pure rule-based fallback (before enough data to train)
            severity = classify_vps_situation(
                cpu=cpu, ram=ram, load_avg_1m=load_avg,
                cpu_cores=cpu_cores, ai_score=0.0,
            )
            return {
                "is_anomaly": severity != "Normal",
                "score":      0.0,
                "severity":   severity,
                "trained":    False,
            }

        try:
            x      = np.array([_to_feature_vector(metric, trend)])
            x_sc   = self.scaler.transform(x)
            score  = float(self.model.score_samples(x_sc)[0])

            severity = classify_vps_situation(
                cpu=cpu, ram=ram, load_avg_1m=load_avg,
                cpu_cores=cpu_cores, ai_score=score,
            )
            return {
                "is_anomaly": severity != "Normal",
                "score":      round(score, 4),
                "severity":   severity,
                "trained":    True,
            }

        except Exception as exc:
            logger.exception("Server %d: prediction failed: %s", self.server_id, exc)
            return {"is_anomaly": False, "score": 0.0, "severity": "Normal", "trained": True}

    # ── Status ───────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "is_trained":             self.is_trained,
            "server_id":              self.server_id,
            "model_path":             self.model_path if self.is_trained else None,
            "contamination":          CONTAMINATION,
            "features":               FEATURE_COLS,
            "feature_count":          len(FEATURE_COLS),
            "train_samples_required": MODEL_TRAIN_SAMPLES,
        }


# ─── Per-server model registry ────────────────────────────────────────────────
# Each server_id gets its own AnomalyDetector instance.
_detectors: dict[int, AnomalyDetector] = {}

# Legacy module-level singleton for code that imports `detector` directly
detector = AnomalyDetector(server_id=0)


def get_detector(server_id: int = 0) -> AnomalyDetector:
    """Return (or create) the AnomalyDetector for the given server."""
    sid = int(server_id or 0)
    if sid not in _detectors:
        _detectors[sid] = AnomalyDetector(sid)
        if sid == 0:
            # Reuse the already-loaded singleton for the local server
            _detectors[0] = detector
    return _detectors[sid]
