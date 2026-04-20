"""
============================================================
app.py — Flask REST API Backend
Project : AI-Based Anomaly Detection System for Cloud Resource Monitoring
Org     : NTPL Digital Private Limited, Noida
Group   : CU - MCA - Group-4
------------------------------------------------------------
API Endpoints:
  POST /api/metrics          ← agent sends data here
  GET  /api/metrics/latest   ← dashboard fetches latest 100 readings
  GET  /api/anomalies        ← dashboard fetches anomaly history
  GET  /api/status           ← returns current server status
  POST /api/train            ← manually trigger model retraining
  GET  /api/alerts           ← returns alert history
============================================================
"""

import os
import sys
import logging
import json
from datetime import datetime, timezone

from flask      import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ── Path resolution: allow imports from project root ─────────────────────────
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from config import (
    API_HOST, API_PORT, API_KEY,
    MODEL_TRAIN_SAMPLES, LOG_LEVEL, LOG_DIR,
    SERVER_NAME, SERVER_IP,
)
from backend.database import (
    init_db, insert_metric, get_latest_metrics,
    get_anomalies, get_alerts, count_metrics,
    get_metrics_for_training,
)
from backend.model   import detector
from backend.alerts  import dispatch_alert

# ─── Logging Setup ───────────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level    = getattr(logging, LOG_LEVEL, logging.INFO),
    format   = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(LOG_DIR, "api.log")),
    ],
)
logger = logging.getLogger(__name__)

# ─── Flask App Init ──────────────────────────────────────────────────────────
DASHBOARD_DIR = os.path.join(ROOT_DIR, "dashboard")

app = Flask(__name__, static_folder=DASHBOARD_DIR, static_url_path="/")
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Initialise database tables on startup
init_db()

# Track latest status for the status endpoint
_latest_status = {
    "status":       "starting",
    "last_updated": None,
    "server_name":  SERVER_NAME,
    "server_ip":    SERVER_IP,
}


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _api_key_valid() -> bool:
    """Check that the X-API-Key header matches the configured key."""
    return request.headers.get("X-API-Key", "") == API_KEY


def _json_error(message: str, code: int = 400):
    return jsonify({"success": False, "error": message}), code


# ─── Dashboard Serve ─────────────────────────────────────────────────────────

@app.route("/")
def serve_dashboard():
    """Serve the main dashboard HTML file."""
    return send_from_directory(DASHBOARD_DIR, "index.html")


# ─── POST /api/metrics ───────────────────────────────────────────────────────

@app.route("/api/metrics", methods=["POST"])
def receive_metrics():
    """
    Receive a metric snapshot from the agent.
    Expected JSON body:
      {
        "timestamp":   "2024-01-01T00:00:00",
        "cpu":         35.2,
        "ram":         61.4,
        "disk":        48.0,
        "network_in":  1024,
        "network_out": 512,
        "processes":   143,
        "uptime":      86400.0
      }
    Requires X-API-Key header.
    """
    global _latest_status

    # ── Authentication ────────────────────────────────────────────────────────
    if not _api_key_valid():
        return _json_error("Unauthorized — invalid or missing X-API-Key", 401)

    # ── Parse body ───────────────────────────────────────────────────────────
    try:
        data = request.get_json(force=True)
        if not data:
            return _json_error("Empty or invalid JSON body")
    except Exception:
        return _json_error("Malformed JSON")

    # ── Validate required fields ──────────────────────────────────────────────
    required = ["cpu", "ram", "disk"]
    for field in required:
        if field not in data:
            return _json_error(f"Missing required field: {field}")

    # Fill optional fields with defaults
    data.setdefault("timestamp",   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
    data.setdefault("network_in",  0)
    data.setdefault("network_out", 0)
    data.setdefault("processes",   0)
    data.setdefault("uptime",      0)

    # ── Store metric ─────────────────────────────────────────────────────────
    try:
        metric_id = insert_metric(data)
    except Exception as exc:
        logger.exception("Failed to insert metric: %s", exc)
        return _json_error("Database write failed", 500)

    # ── Run anomaly detection ─────────────────────────────────────────────────
    prediction = detector.predict(data)
    is_anomaly = prediction["is_anomaly"]
    severity   = prediction["severity"]
    score      = prediction["score"]

    # If anomaly, log it and trigger alert
    if is_anomaly:
        try:
            from backend.database import insert_anomaly
            insert_anomaly(data, score, severity)
        except Exception as exc:
            logger.error("Failed to insert anomaly record: %s", exc)
        dispatch_alert(severity, score, data)

    # Auto-train: once we have enough data and the model is not yet trained
    total = count_metrics()
    if not detector.is_trained and total >= MODEL_TRAIN_SAMPLES:
        logger.info("Auto-training model on %d samples …", total)
        training_data = get_metrics_for_training(MODEL_TRAIN_SAMPLES)
        detector.train(training_data)

    # Update latest status cache
    _latest_status.update({
        "status":       "anomaly" if is_anomaly else "normal",
        "last_updated": data["timestamp"],
        "severity":     severity if is_anomaly else None,
        "score":        score,
        "metric_id":    metric_id,
        "total_metrics": total,
    })

    return jsonify({
        "success":    True,
        "metric_id":  metric_id,
        "prediction": prediction,
    }), 201


# ─── GET /api/metrics/latest ─────────────────────────────────────────────────

@app.route("/api/metrics/latest", methods=["GET"])
def latest_metrics():
    """Return the last 100 metric readings (chronological order)."""
    try:
        limit   = int(request.args.get("limit", 100))
        metrics = get_latest_metrics(limit)
        return jsonify({"success": True, "data": metrics, "count": len(metrics)})
    except Exception as exc:
        logger.exception("latest_metrics error: %s", exc)
        return _json_error("Failed to fetch metrics", 500)


# ─── GET /api/anomalies ───────────────────────────────────────────────────────

@app.route("/api/anomalies", methods=["GET"])
def anomaly_history():
    """Return the most recent anomaly records."""
    try:
        limit     = int(request.args.get("limit", 50))
        anomalies = get_anomalies(limit)
        return jsonify({"success": True, "data": anomalies, "count": len(anomalies)})
    except Exception as exc:
        logger.exception("anomaly_history error: %s", exc)
        return _json_error("Failed to fetch anomalies", 500)


# ─── GET /api/status ─────────────────────────────────────────────────────────

@app.route("/api/status", methods=["GET"])
def server_status():
    """Return current monitoring status and model info."""
    try:
        return jsonify({
            "success":      True,
            "server_name":  SERVER_NAME,
            "server_ip":    SERVER_IP,
            "data": {
                **_latest_status,
                "model":        detector.status(),
                "total_metrics": count_metrics(),
            },
        })
    except Exception as exc:
        logger.exception("server_status error: %s", exc)
        return _json_error("Failed to fetch status", 500)


# ─── POST /api/train ─────────────────────────────────────────────────────────

@app.route("/api/train", methods=["POST"])
def manual_train():
    """
    Manually trigger model retraining on the most recent readings.
    Requires X-API-Key header.
    """
    if not _api_key_valid():
        return _json_error("Unauthorized", 401)

    try:
        limit         = int(request.args.get("limit", MODEL_TRAIN_SAMPLES))
        training_data = get_metrics_for_training(limit)

        if len(training_data) < MODEL_TRAIN_SAMPLES:
            return _json_error(
                f"Not enough data. Need {MODEL_TRAIN_SAMPLES}, have {len(training_data)}.", 400
            )

        ok = detector.train(training_data)
        if ok:
            return jsonify({"success": True, "message": "Model retrained successfully.", "samples": len(training_data)})
        return _json_error("Training failed. Check logs.", 500)

    except Exception as exc:
        logger.exception("manual_train error: %s", exc)
        return _json_error("Training error", 500)


# ─── GET /api/alerts ─────────────────────────────────────────────────────────

@app.route("/api/alerts", methods=["GET"])
def alert_history():
    """Return the most recent alert records."""
    try:
        limit  = int(request.args.get("limit", 50))
        alerts = get_alerts(limit)
        return jsonify({"success": True, "data": alerts, "count": len(alerts)})
    except Exception as exc:
        logger.exception("alert_history error: %s", exc)
        return _json_error("Failed to fetch alerts", 500)


# ─── Health Check ────────────────────────────────────────────────────────────

@app.route("/api/health", methods=["GET"])
def health():
    """Simple ping endpoint for load-balancer health checks."""
    return jsonify({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})


# ─── Error Handlers ──────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(_):
    return _json_error("Endpoint not found", 404)


@app.errorhandler(405)
def method_not_allowed(_):
    return _json_error("Method not allowed", 405)


@app.errorhandler(500)
def internal_error(exc):
    logger.exception("Unhandled exception: %s", exc)
    return _json_error("Internal server error", 500)


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting Flask API on %s:%d", API_HOST, API_PORT)
    app.run(host=API_HOST, port=API_PORT, debug=False)
