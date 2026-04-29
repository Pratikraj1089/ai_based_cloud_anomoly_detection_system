"""
============================================================
app.py -- Flask REST API Backend
Project : AI-Based Anomaly Detection System for Cloud Resource Monitoring
Org     : NTPL Digital Private Limited, Noida
Group   : CU - MCA - Group-4
------------------------------------------------------------
API Endpoints:
  POST /api/metrics          <- agent sends data here
  GET  /api/metrics/latest   <- dashboard fetches latest 100 readings
  GET  /api/anomalies        <- dashboard fetches anomaly history
  GET  /api/status           <- returns current server status
  POST /api/train            <- manually trigger model retraining
  GET  /api/alerts           <- returns alert history
  GET  /api/servers          <- list saved VPS servers
  POST /api/servers          <- add a new VPS server
  POST /api/server/connect   <- SSH into a VPS and fetch live metrics
============================================================
"""

import os
import sys
import logging
import json
from datetime import datetime, timezone

from flask      import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# -- Path resolution: allow imports from project root -------------------------
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

# --- Logging Setup -----------------------------------------------------------
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

# --- Flask App Init ----------------------------------------------------------
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


# --- Helpers -----------------------------------------------------------------

def _api_key_valid() -> bool:
    """Check that the X-API-Key header matches the configured key."""
    return request.headers.get("X-API-Key", "") == API_KEY


def _json_error(message: str, code: int = 400):
    return jsonify({"success": False, "error": message}), code


# --- Dashboard Serve ---------------------------------------------------------

@app.route("/")
def serve_dashboard():
    """Serve the main dashboard HTML file."""
    return send_from_directory(DASHBOARD_DIR, "index.html")


# --- POST /api/metrics -------------------------------------------------------

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

    if not _api_key_valid():
        return _json_error("Unauthorized -- invalid or missing X-API-Key", 401)

    try:
        data = request.get_json(force=True)
        if not data:
            return _json_error("Empty or invalid JSON body")
    except Exception:
        return _json_error("Malformed JSON")

    required = ["cpu", "ram", "disk"]
    for field in required:
        if field not in data:
            return _json_error(f"Missing required field: {field}")

    data.setdefault("timestamp",   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"))
    data.setdefault("network_in",  0)
    data.setdefault("network_out", 0)
    data.setdefault("processes",   0)
    data.setdefault("uptime",      0)

    try:
        metric_id = insert_metric(data)
    except Exception as exc:
        logger.exception("Failed to insert metric: %s", exc)
        return _json_error("Database write failed", 500)

    prediction = detector.predict(data)
    is_anomaly = prediction["is_anomaly"]
    severity   = prediction["severity"]
    score      = prediction["score"]

    if is_anomaly:
        try:
            from backend.database import insert_anomaly
            insert_anomaly(data, score, severity)
        except Exception as exc:
            logger.error("Failed to insert anomaly record: %s", exc)
        dispatch_alert(severity, score, data)

    total = count_metrics()
    if not detector.is_trained and total >= MODEL_TRAIN_SAMPLES:
        logger.info("Auto-training model on %d samples ...", total)
        training_data = get_metrics_for_training(MODEL_TRAIN_SAMPLES)
        detector.train(training_data)

    _latest_status.update({
        "status":        "anomaly" if is_anomaly else "normal",
        "last_updated":  data["timestamp"],
        "severity":      severity if is_anomaly else None,
        "score":         score,
        "metric_id":     metric_id,
        "total_metrics": total,
    })

    return jsonify({
        "success":    True,
        "metric_id":  metric_id,
        "prediction": prediction,
    }), 201


# --- GET /api/metrics/latest -------------------------------------------------

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


# --- GET /api/anomalies ------------------------------------------------------

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


# --- GET /api/status ---------------------------------------------------------

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
                "model":         detector.status(),
                "total_metrics": count_metrics(),
            },
        })
    except Exception as exc:
        logger.exception("server_status error: %s", exc)
        return _json_error("Failed to fetch status", 500)


# --- POST /api/train ---------------------------------------------------------

@app.route("/api/train", methods=["POST"])
def manual_train():
    """Manually trigger model retraining. Requires X-API-Key header."""
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


# --- GET /api/alerts ---------------------------------------------------------

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


# --- GET /api/servers --------------------------------------------------------

@app.route("/api/servers", methods=["GET"])
def list_servers():
    """Return all configured VPS servers (no passwords stored)."""
    try:
        from backend.database import get_servers
        servers = get_servers()
        return jsonify({"success": True, "data": servers})
    except Exception as exc:
        logger.exception("list_servers error: %s", exc)
        return _json_error("Failed to fetch servers", 500)


# --- POST /api/servers -------------------------------------------------------

@app.route("/api/servers", methods=["POST"])
def add_new_server():
    """Add a new VPS server (stores name, ip, port, username -- never password)."""
    try:
        data = request.get_json(force=True)
        if not data or "ip" not in data or "username" not in data or "name" not in data:
            return _json_error("Missing required fields (name, ip, username)")
        from backend.database import add_server
        sid = add_server(data["name"], data["ip"], data.get("port", 22), data["username"])
        return jsonify({"success": True, "server_id": sid})
    except Exception as exc:
        logger.exception("add_new_server error: %s", exc)
        return _json_error("Failed to add server", 500)


# --- POST /api/server/connect ------------------------------------------------

# SSH metric collector script -- mirrors the local psutil agent exactly.
# Reads from /proc/* for accuracy (handles suspend/resume).
# Returns a single JSON line with all fields.
_SSH_METRIC_SCRIPT = """
# CPU usage % from /proc/stat (first line = aggregate)
cpu_line=$(head -1 /proc/stat)
set -- $cpu_line
shift  # remove "cpu" tag
user=$1 nice=$2 sys=$3 idle=$4 iowait=$5 irq=$6 sirq=$7 steal=$8
total=$((user+nice+sys+idle+iowait+irq+sirq+steal))
used=$((total-idle))
cpu_pct=$(awk "BEGIN{printf \\"%.1f\\", ($used/$total)*100}")

# RAM usage % from /proc/meminfo
mem_total=$(awk '/MemTotal/{print $2}'    /proc/meminfo)
mem_avail=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
ram_pct=$(awk "BEGIN{printf \\"%.1f\\", (($mem_total-$mem_avail)/$mem_total)*100}")

# Disk usage % for root partition
disk_pct=$(df / --output=pcent 2>/dev/null | tail -1 | tr -d ' %')

# Process count
procs=$(ps -e --no-headers 2>/dev/null | wc -l)

# Uptime in seconds (accurate through sleep/resume)
uptime_sec=$(cut -d' ' -f1 /proc/uptime)

# Cumulative network bytes (all non-loopback interfaces)
net_rx=$(awk 'NR>2 && !/lo:/{sum+=$2}  END{printf "%d",sum+0}' /proc/net/dev)
net_tx=$(awk 'NR>2 && !/lo:/{sum+=$10} END{printf "%d",sum+0}' /proc/net/dev)

printf '{"cpu":%s,"ram":%s,"disk":%s,"processes":%d,"uptime":%s,"net_rx_bytes":%s,"net_tx_bytes":%s}' \
    "$cpu_pct" "$ram_pct" "$disk_pct" "$procs" "$uptime_sec" "$net_rx" "$net_tx"
"""


@app.route("/api/server/connect", methods=["POST"])
def connect_server():
    """
    SSH into a saved VPS, collect full system metrics, run anomaly detection.

    The password is used only for this single SSH session and is NEVER stored.
    Returns:
      - metrics      : dict with cpu, ram, disk, processes, uptime, network_in/out
      - net_rx_bytes : cumulative RX bytes (dashboard calculates per-interval rate)
      - net_tx_bytes : cumulative TX bytes
      - prediction   : anomaly detection result
      - server_name  : display name of the server
      - server_ip    : IP address of the server
    """
    try:
        body      = request.get_json(force=True)
        server_id = body.get("server_id")
        password  = body.get("password")
        if not server_id or not password:
            return _json_error("Missing server_id or password")

        from backend.database import get_servers
        servers = get_servers()
        server  = next((s for s in servers if s["id"] == int(server_id)), None)
        if not server:
            return _json_error("Server not found", 404)

        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            server["ip"],
            port=server["port"],
            username=server["username"],
            password=password,
            timeout=10,
        )

        _, stdout, stderr = ssh.exec_command(_SSH_METRIC_SCRIPT)
        output = stdout.read().decode("utf-8").strip()
        err    = stderr.read().decode("utf-8").strip()
        ssh.close()

        if not output:
            logger.error("SSH script returned empty output. stderr: %s", err)
            return _json_error("No output from remote script -- check user permissions", 500)

        try:
            raw = json.loads(output)
        except json.JSONDecodeError:
            logger.error("JSON parse error. output=%r stderr=%s", output, err)
            return _json_error("Failed to parse remote metrics", 500)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        # network_in/out kept as 0 for anomaly scoring (model trained on rates,
        # not cumulative bytes); the real cumulative bytes are returned separately
        # so the dashboard can compute the rate between successive calls.
        metrics = {
            "cpu":         raw.get("cpu",       0),
            "ram":         raw.get("ram",       0),
            "disk":        raw.get("disk",      0),
            "processes":   raw.get("processes", 0),
            "uptime":      raw.get("uptime",    0),
            "network_in":  0,
            "network_out": 0,
            "timestamp":   ts,
        }

        prediction = detector.predict(metrics)

        return jsonify({
            "success":      True,
            "metrics":      metrics,
            "net_rx_bytes": raw.get("net_rx_bytes", 0),
            "net_tx_bytes": raw.get("net_tx_bytes", 0),
            "server_name":  server["name"],
            "server_ip":    server["ip"],
            "prediction":   prediction,
        })

    except paramiko.AuthenticationException:
        return _json_error("Authentication failed -- incorrect password", 401)
    except Exception as exc:
        logger.exception("connect_server error: %s", exc)
        return _json_error("Connection failed: " + str(exc), 500)


# --- Health Check ------------------------------------------------------------

@app.route("/api/health", methods=["GET"])
def health():
    """Simple ping endpoint for load-balancer health checks."""
    return jsonify({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})


# --- Error Handlers ----------------------------------------------------------

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


# --- Entry Point -------------------------------------------------------------

if __name__ == "__main__":
    logger.info("Starting Flask API on %s:%d", API_HOST, API_PORT)
    app.run(host=API_HOST, port=API_PORT, debug=False)
