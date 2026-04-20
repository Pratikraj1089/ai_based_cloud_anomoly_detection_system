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

logger = logging.getLogger(__name__)

# Feature columns used for training / prediction (must match metrics table)
FEATURE_COLS = ["cpu", "ram", "disk", "network_in", "network_out", "processes"]


# ─── Utility: extract feature vector from a metric dict ──────────────────────

def _to_feature_vector(metric: dict) -> list[float]:
    """Convert a metric dictionary to a fixed-order feature list."""
    return [float(metric.get(col, 0)) for col in FEATURE_COLS]


# ─── Model State ─────────────────────────────────────────────────────────────

class AnomalyDetector:
    """
    Wraps sklearn's IsolationForest with:
      - StandardScaler for normalisation
      - Persistent save / load via joblib
      - Simple severity classification based on anomaly score
    """

    def __init__(self):
        self.model: IsolationForest | None = None
        self.scaler: StandardScaler | None = None
        self.is_trained: bool = False
        self._try_load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _try_load(self) -> None:
        """Attempt to load a previously saved model from disk."""
        if os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH):
            try:
                self.model  = joblib.load(MODEL_PATH)
                self.scaler = joblib.load(SCALER_PATH)
                self.is_trained = True
                logger.info("Loaded existing model from %s", MODEL_PATH)
            except Exception as exc:
                logger.warning("Could not load saved model (%s). Will retrain.", exc)

    def _save(self) -> None:
        """Persist the current model and scaler to disk."""
        try:
            os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
            joblib.dump(self.model,  MODEL_PATH)
            joblib.dump(self.scaler, SCALER_PATH)
            logger.info("Model saved to %s", MODEL_PATH)
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
            "severity":   str,     # "normal" | "low" | "medium" | "high"
            "trained":    bool,    # whether the model has been trained
          }
        """
        if not self.is_trained:
            return {
                "is_anomaly": False,
                "score":      0.0,
                "severity":   "normal",
                "trained":    False,
            }

        try:
            x       = np.array([_to_feature_vector(metric)])
            x_sc    = self.scaler.transform(x)
            score   = float(self.model.score_samples(x_sc)[0])
            severity = self._classify_severity(score)

            return {
                "is_anomaly": severity != "normal",
                "score":      round(score, 4),
                "severity":   severity,
                "trained":    True,
            }

        except Exception as exc:
            logger.exception("Prediction failed: %s", exc)
            return {
                "is_anomaly": False,
                "score":      0.0,
                "severity":   "normal",
                "trained":    True,
            }

    # ── Severity Classification ───────────────────────────────────────────────

    @staticmethod
    def _classify_severity(score: float) -> str:
        """Map an anomaly score to a human-readable severity level."""
        if score < THRESHOLD_HIGH:
            return "high"
        if score < THRESHOLD_MEDIUM:
            return "medium"
        if score < THRESHOLD_LOW:
            return "low"
        return "normal"

    # ── Model Status ─────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Return a summary of the current model state."""
        return {
            "is_trained":    self.is_trained,
            "model_path":    MODEL_PATH if self.is_trained else None,
            "contamination": CONTAMINATION,
            "features":      FEATURE_COLS,
            "thresholds": {
                "low":    THRESHOLD_LOW,
                "medium": THRESHOLD_MEDIUM,
                "high":   THRESHOLD_HIGH,
            },
        }


# ─── Module-level singleton ───────────────────────────────────────────────────
detector = AnomalyDetector()
