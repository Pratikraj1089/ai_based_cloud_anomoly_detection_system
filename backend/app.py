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
import threading
import time
from datetime import datetime, timezone

from flask      import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_sock import Sock

# -- Path resolution: allow imports from project root -------------------------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import config
from config import (
    API_HOST, API_PORT, API_KEY,
    MODEL_TRAIN_SAMPLES, LOG_LEVEL, LOG_DIR,
    SERVER_NAME, SERVER_IP, AGENT_INTERVAL_SECONDS,
)
from backend.database import (
    init_db, insert_metric, get_latest_metrics,
    get_anomalies, get_alerts, count_metrics,
    get_metrics_for_training,
)
from backend.model   import get_detector
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
sock = Sock(app)

# Initialise database tables on startup
init_db()

# Track latest statuses by server_id
_latest_statuses = {}

def get_latest_status(server_id: int) -> dict:
    server_id = int(server_id or 0)
    if server_id not in _latest_statuses:
        _latest_statuses[server_id] = {
            "status":       "starting",
            "last_updated": None,
            "server_name":  SERVER_NAME if server_id == 0 else f"VPS-{server_id}",
            "server_ip":    SERVER_IP if server_id == 0 else "",
            "severity":     None,
            "score":        0.0,
            "metric_id":    None,
            "total_metrics": 0,
        }
    return _latest_statuses[server_id]


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
    data.setdefault("server_id",   0)

    try:
        metric_id = insert_metric(data)
    except Exception as exc:
        logger.exception("Failed to insert metric: %s", exc)
        return _json_error("Database write failed", 500)

    from backend.trend_engine import push_metric, get_trend_features
    push_metric(0, data)
    trend = get_trend_features(0, data)

    prediction = get_detector(0).predict(data, trend)
    is_anomaly = prediction["is_anomaly"]
    severity   = prediction["severity"]
    score      = prediction["score"]

    # Calculate network ratios and baselines
    net_in = float(data.get("network_in", 0.0))
    net_out = float(data.get("network_out", 0.0))
    net_in_avg = float(trend.get("net_in_5min_avg", net_in))
    net_out_avg = float(trend.get("net_out_5min_avg", net_out))
    from config import NET_MIN_SAFE_BASELINE
    net_in_ratio = net_in / max(net_in_avg, NET_MIN_SAFE_BASELINE)
    net_out_ratio = net_out / max(net_out_avg, NET_MIN_SAFE_BASELINE)

    from backend.false_positive_suppressor import should_alert
    alert_approved = should_alert(0, is_anomaly)

    if is_anomaly:
        try:
            from backend.explainer import explain
            from backend.root_cause import classify_root_cause
            from backend.database import insert_anomaly
            
            reasons = explain(data, trend, prediction)
            consec_ram = trend.get("consecutive_ram_increases", 0)
            probable_cause = classify_root_cause(data, trend, consec_ram)
            
            insert_anomaly(data, score, severity, 0, reasons, probable_cause,
                           network_in_ratio=net_in_ratio, network_out_ratio=net_out_ratio)
        except Exception as exc:
            logger.error("Failed to insert anomaly record: %s", exc)
            
        if alert_approved:
            dispatch_alert(severity, score, data)

    total = count_metrics(0)
    detector = get_detector(0)
    if not detector.is_trained and total >= MODEL_TRAIN_SAMPLES:
        logger.info("Auto-training model on %d samples ...", total)
        training_data = get_metrics_for_training(MODEL_TRAIN_SAMPLES, 0)
        detector.train(training_data)

    get_latest_status(0).update({
        "status":             "anomaly" if is_anomaly else "normal",
        "last_updated":       data["timestamp"],
        "severity":           severity if is_anomaly else None,
        "score":              score,
        "metric_id":          metric_id,
        "total_metrics":      total,
        "network_in_ratio":   round(net_in_ratio, 2),
        "network_out_ratio":  round(net_out_ratio, 2),
        "net_in_5min_avg":    round(net_in_avg, 2),
        "net_out_5min_avg":   round(net_out_avg, 2),
    })

    return jsonify({
        "success":    True,
        "metric_id":  metric_id,
        "prediction": prediction,
    }), 201


# --- GET /api/metrics/latest -------------------------------------------------

@app.route("/api/metrics/latest", methods=["GET"])
def latest_metrics():
    """Return the last 100 metric readings (chronological order) for a server."""
    try:
        limit     = int(request.args.get("limit", 100))
        server_id = request.args.get("server_id", None)
        if server_id is not None:
            server_id = int(server_id)
        metrics = get_latest_metrics(limit, server_id)
        return jsonify({"success": True, "data": metrics, "count": len(metrics)})
    except Exception as exc:
        logger.exception("latest_metrics error: %s", exc)
        return _json_error("Failed to fetch metrics", 500)


# --- GET /api/anomalies ------------------------------------------------------

@app.route("/api/anomalies", methods=["GET"])
def anomaly_history():
    """Return the most recent anomaly records for a server."""
    try:
        limit     = int(request.args.get("limit", 50))
        server_id = request.args.get("server_id", None)
        if server_id is not None:
            server_id = int(server_id)
        anomalies = get_anomalies(limit, server_id)
        return jsonify({"success": True, "data": anomalies, "count": len(anomalies)})
    except Exception as exc:
        logger.exception("anomaly_history error: %s", exc)
        return _json_error("Failed to fetch anomalies", 500)


# --- GET /api/status ---------------------------------------------------------

@app.route("/api/status", methods=["GET"])
def server_status():
    """Return current monitoring status and model info for a server."""
    try:
        server_id = request.args.get("server_id", 0, type=int)
        detector = get_detector(server_id)
        
        name = SERVER_NAME
        ip = SERVER_IP
        if server_id != 0:
            server = get_server_by_id(server_id)
            if server:
                name = server["name"]
                ip = server["ip"]
                
        status_data = get_latest_status(server_id)
        
        return jsonify({
            "success":      True,
            "server_name":  name,
            "server_ip":    ip,
            "data": {
                **status_data,
                "model":         detector.status(),
                "total_metrics": count_metrics(server_id),
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
        server_id     = request.args.get("server_id", 0, type=int)
        limit         = int(request.args.get("limit", MODEL_TRAIN_SAMPLES))
        training_data = get_metrics_for_training(limit, server_id)

        if len(training_data) < MODEL_TRAIN_SAMPLES:
            return _json_error(
                f"Not enough data. Need {MODEL_TRAIN_SAMPLES}, have {len(training_data)}.", 400
            )

        detector = get_detector(server_id)
        ok = detector.train(training_data)
        if ok:
            return jsonify({"success": True, "message": "Model retrained successfully.", "samples": len(training_data)})
        return _json_error("Training failed. Check logs.", 500)

    except Exception as exc:
        logger.exception("manual_train error: %s", exc)
        return _json_error("Training error", 500)


# --- POST /api/test-email ----------------------------------------------------

@app.route("/api/test-email", methods=["POST"])
def test_email():
    """Send a test incident email to verify SMTP credentials."""
    try:
        from backend.email_alerts import verify_smtp_credentials, send_alert_email
        
        # Verify first
        verify_res = verify_smtp_credentials()
        
        # Build a mock metric payload for the test email
        mock_metric = {
            "cpu": 92.4,
            "ram": 88.1,
            "disk": 75.3,
            "processes": 245,
            "network_in": 12500000.0,
            "network_out": 4800000.0,
            "load_avg_1m": 8.5,
            "cpu_cores": 4,
            "uptime": 345600.0,
            "server_id": 0
        }
        mock_trend = {
            "cpu_delta": 24.5,
            "ram_delta": 6.2,
            "disk_delta": 0.1,
            "process_delta": 18,
            "net_in_delta": 5000000.0,
            "net_out_delta": 1200000.0,
            "cpu_5min_avg": 81.2,
            "ram_5min_avg": 84.5,
            "net_in_5min_avg": 10000000.0,
            "proc_5min_avg": 230.0
        }
        mock_reasons = [
            "Test Alert: CPU usage is critically high (92.4%)",
            "Test Alert: Process count spike detected (+18)",
            "Test Alert: Load ratio exceeds safe limits"
        ]
        mock_probable_cause = "Test Simulation / SMTP Verification"

        if verify_res["smtp_connection"] and verify_res["authentication"]:
            # Connection and Auth ok, try sending the email
            sent_ok = send_alert_email(
                server_id=0,
                severity="Danger",
                score=-0.8521,
                metric=mock_metric,
                trend=mock_trend,
                reasons=mock_reasons,
                probable_cause=mock_probable_cause
            )
            verify_res["email_sent"] = sent_ok
            if sent_ok:
                verify_res["message"] = "Test email delivered successfully"
            else:
                verify_res["message"] = "SMTP login succeeded, but email transmission failed"
        else:
            verify_res["email_sent"] = False
            
        return jsonify(verify_res)
    except Exception as exc:
        logger.exception("test_email error: %s", exc)
        return jsonify({
            "smtp_connection": False,
            "authentication": False,
            "email_sent": False,
            "message": f"Test email failed: {str(exc)}"
        }), 500


# --- GET /api/email-status ---------------------------------------------------

@app.route("/api/email-status", methods=["GET"])
def email_status():
    """Return SMTP verification status and email alerting statistics."""
    try:
        from backend.email_alerts import verify_smtp_credentials
        from backend.database import get_email_alert_stats
        
        verify_res = verify_smtp_credentials()
        stats = get_email_alert_stats()
        
        return jsonify({
            "success": True,
            "smtp_connection": verify_res["smtp_connection"],
            "authentication": verify_res["authentication"],
            "smtp_status_message": verify_res["message"],
            "email_enabled": config.SMTP_ENABLED,
            **stats
        })
    except Exception as exc:
        logger.exception("email_status error: %s", exc)
        return _json_error("Failed to fetch email status", 500)


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
# CPU usage over 1 second (interval rate)
read -r tag u1 n1 s1 i1 io1 ir1 si1 st1 _ < /proc/stat
prev_total=$((u1+n1+s1+i1+io1+ir1+si1+st1))
prev_idle=$((i1+io1))

sleep 1

read -r tag u2 n2 s2 i2 io2 ir2 si2 st2 _ < /proc/stat
total=$((u2+n2+s2+i2+io2+ir2+si2+st2))
idle=$((i2+io2))

diff_total=$((total-prev_total))
diff_idle=$((idle-prev_idle))
diff_used=$((diff_total-diff_idle))

if [ "$diff_total" -eq 0 ]; then
    cpu_pct="0.0"
else
    cpu_pct=$(awk "BEGIN{printf \\"%.1f\\", ($diff_used/$diff_total)*100}")
fi

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


# --- SSH Connection Pool & Background Polling --------------------------------

class SSHConnectionPool:
    def __init__(self):
        self.connections = {}  # server_id -> SSHClient
        self.passwords = {}    # server_id -> password
        self.net_stats = {}    # server_id -> {"last_rx": float, "last_tx": float, "last_ts": float}
        self.lock = threading.Lock()

    def add(self, server_id, ssh, password):
        server_id = int(server_id)
        with self.lock:
            if server_id in self.connections:
                try:
                    self.connections[server_id].close()
                except Exception:
                    pass
            self.connections[server_id] = ssh
            self.passwords[server_id] = password

    def get(self, server_id):
        server_id = int(server_id)
        with self.lock:
            return self.connections.get(server_id)

    def remove(self, server_id):
        server_id = int(server_id)
        with self.lock:
            ssh = self.connections.pop(server_id, None)
            self.passwords.pop(server_id, None)
            self.net_stats.pop(server_id, None)
            if ssh:
                try:
                    ssh.close()
                except Exception:
                    pass

    def get_active_sessions(self):
        with self.lock:
            active = {}
            for sid, ssh in list(self.connections.items()):
                try:
                    if ssh.get_transport() and ssh.get_transport().is_active():
                        active[sid] = ssh
                    else:
                        # try to reconnect using cached credentials
                        password = self.passwords.get(sid)
                        if password:
                            server = get_server_by_id(sid)
                            if server:
                                logger.info("Reconnecting to server %d ...", sid)
                                import paramiko
                                new_ssh = paramiko.SSHClient()
                                new_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                                new_ssh.connect(
                                    server["ip"], port=server["port"],
                                    username=server["username"], password=password,
                                    timeout=10, allow_agent=False, look_for_keys=False
                                )
                                self.connections[sid] = new_ssh
                                active[sid] = new_ssh
                                continue
                        # If reconnect fails, remove it
                        self.connections.pop(sid, None)
                except Exception as exc:
                    logger.warning("Session validation/reconnect failed for server %d: %s", sid, exc)
                    self.connections.pop(sid, None)
            return active

    def update_net_stats(self, server_id, rx, tx, ts):
        server_id = int(server_id)
        with self.lock:
            self.net_stats[server_id] = {"last_rx": rx, "last_tx": tx, "last_ts": ts}

    def get_net_stats(self, server_id):
        server_id = int(server_id)
        with self.lock:
            return self.net_stats.get(server_id)


def get_server_by_id(server_id):
    from backend.database import get_servers
    servers = get_servers()
    return next((s for s in servers if s["id"] == int(server_id)), None)


ssh_pool = SSHConnectionPool()
_metrics_ws_clients = set()


def broadcast_metrics_update(server_id, metrics, prediction):
    payload = json.dumps({
        "server_id": server_id,
        "metrics": metrics,
        "prediction": prediction,
    })
    for client in list(_metrics_ws_clients):
        try:
            client.send(payload)
        except Exception:
            _metrics_ws_clients.discard(client)


def background_poll_loop():
    logger.info("Background metrics polling thread started.")
    while True:
        try:
            time.sleep(AGENT_INTERVAL_SECONDS)
            
            active_sessions = ssh_pool.get_active_sessions()
            for server_id, ssh in active_sessions.items():
                try:
                    _, stdout, stderr = ssh.exec_command(_SSH_METRIC_SCRIPT)
                    output = stdout.read().decode("utf-8").strip()
                    if not output:
                        continue
                    
                    raw = json.loads(output)
                    server = get_server_by_id(server_id)
                    if not server:
                        continue
                    
                    now_ts = time.time()
                    stats = ssh_pool.get_net_stats(server_id)
                    
                    net_in = 0.0
                    net_out = 0.0
                    curr_rx = float(raw.get("net_rx_bytes", 0))
                    curr_tx = float(raw.get("net_tx_bytes", 0))
                    
                    if stats:
                        dt = now_ts - stats["last_ts"]
                        if dt > 0:
                            net_in = max(0.0, (curr_rx - stats["last_rx"]) / dt)
                            net_out = max(0.0, (curr_tx - stats["last_tx"]) / dt)
                    
                    ssh_pool.update_net_stats(server_id, curr_rx, curr_tx, now_ts)
                    
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                    metrics = {
                        "cpu":         raw.get("cpu",       0),
                        "ram":         raw.get("ram",       0),
                        "disk":        raw.get("disk",      0),
                        "network_in":  round(net_in, 2),
                        "network_out": round(net_out, 2),
                        "processes":   raw.get("processes", 0),
                        "uptime":      raw.get("uptime",    0),
                        "server_id":   server_id,
                        "timestamp":   ts,
                    }
                    
                    # Save to DB
                    metric_id = insert_metric(metrics)
                    
                    # Run anomaly detection
                    from backend.trend_engine import push_metric, get_trend_features
                    push_metric(server_id, metrics)
                    trend = get_trend_features(server_id, metrics)

                    detector = get_detector(server_id)
                    prediction = detector.predict(metrics, trend)
                    
                    is_anomaly = prediction["is_anomaly"]
                    severity   = prediction["severity"]
                    score      = prediction["score"]
                    
                    # Calculate network ratios and baselines for remote server
                    net_in = float(metrics.get("network_in", 0.0))
                    net_out = float(metrics.get("network_out", 0.0))
                    net_in_avg = float(trend.get("net_in_5min_avg", net_in))
                    net_out_avg = float(trend.get("net_out_5min_avg", net_out))
                    from config import NET_MIN_SAFE_BASELINE
                    net_in_ratio = net_in / max(net_in_avg, NET_MIN_SAFE_BASELINE)
                    net_out_ratio = net_out / max(net_out_avg, NET_MIN_SAFE_BASELINE)

                    from backend.false_positive_suppressor import should_alert
                    alert_approved = should_alert(server_id, is_anomaly)
                    
                    if is_anomaly:
                        try:
                            from backend.explainer import explain
                            from backend.root_cause import classify_root_cause
                            from backend.database import insert_anomaly
                            
                            reasons = explain(metrics, trend, prediction)
                            consec_ram = trend.get("consecutive_ram_increases", 0)
                            probable_cause = classify_root_cause(metrics, trend, consec_ram)
                            
                            insert_anomaly(metrics, score, severity, server_id, reasons, probable_cause,
                                           network_in_ratio=net_in_ratio, network_out_ratio=net_out_ratio)
                        except Exception as e:
                            logger.error("Failed to insert remote anomaly record: %s", e)
                            
                        if alert_approved:
                            dispatch_alert(severity, score, metrics)
                        
                    total_samples = count_metrics(server_id)
                    if not detector.is_trained and total_samples >= MODEL_TRAIN_SAMPLES:
                        logger.info("Auto-training model for server %d on %d samples...", server_id, total_samples)
                        training_data = get_metrics_for_training(MODEL_TRAIN_SAMPLES, server_id)
                        detector.train(training_data)
                        
                    get_latest_status(server_id).update({
                        "status":             "anomaly" if is_anomaly else "normal",
                        "last_updated":       ts,
                        "severity":           severity if is_anomaly else None,
                        "score":              score,
                        "metric_id":          metric_id,
                        "total_metrics":      total_samples,
                        "network_in_ratio":   round(net_in_ratio, 2),
                        "network_out_ratio":  round(net_out_ratio, 2),
                        "net_in_5min_avg":    round(net_in_avg, 2),
                        "net_out_5min_avg":   round(net_out_avg, 2),
                    })
                    
                    broadcast_metrics_update(server_id, metrics, prediction)
                    
                except Exception as exc:
                    logger.error("Error polling metrics for server %d: %s", server_id, exc)
                    
        except Exception as exc:
            logger.exception("Error in background_poll_loop: %s", exc)


# --- WebSockets --------------------------------------------------------------

@sock.route('/ws/metrics')
def metrics_ws(ws):
    logger.info("Dashboard metrics WS client connected.")
    _metrics_ws_clients.add(ws)
    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
    finally:
        _metrics_ws_clients.discard(ws)
        logger.info("Dashboard metrics WS client disconnected.")


@sock.route('/ws/terminal')
def terminal_ws(ws):
    import pty
    import os
    import select
    import subprocess
    import threading

    server_id = request.args.get("server_id")
    logger.info("Terminal WS: Connection request for server_id: %s", server_id)
    if not server_id:
        logger.warning("Terminal WS: Missing server_id")
        ws.send("Error: Missing server_id\r\n")
        ws.close()
        return

    server = get_server_by_id(server_id)
    if not server:
        logger.warning("Terminal WS: Server not found for server_id: %s", server_id)
        ws.send(f"Error: Server with ID {server_id} not found in database.\r\n")
        ws.close()
        return

    ip = server["ip"]
    username = server["username"] or "root"
    port = str(server.get("port", 22))

    cmd = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        f"{username}@{ip}",
        "-p", port
    ]
    logger.info("Terminal WS: Spawning command: %s", " ".join(cmd))

    try:
        pid, fd = pty.fork()
    except Exception as exc:
        logger.exception("Terminal WS: Failed to fork PTY")
        ws.send(f"Error spawning pseudo-terminal: {exc}\r\n")
        ws.close()
        return

    if pid == 0:
        # Child process: execute the SSH command
        try:
            os.execvp("ssh", cmd)
        except Exception:
            os._exit(1)

    # Parent process: handle stream between PTY and WS
    def stream_pty_to_ws():
        logger.info("Terminal WS: Starting PTY to WebSocket stream thread for server_id: %s", server_id)
        try:
            while True:
                # Use select to check if fd is readable with a 50ms timeout
                r, _, _ = select.select([fd], [], [], 0.05)
                if fd in r:
                    try:
                        data = os.read(fd, 1024)
                    except OSError:
                        # Raised when the child process exits/hangs up
                        break
                    if not data:
                        break
                    ws.send(data.decode("utf-8", errors="ignore"))
        except Exception as e:
            logger.error("Terminal WS: Exception in PTY-to-WS stream for server_id %s: %s", server_id, e)
        finally:
            logger.info("Terminal WS: Closing PTY to WebSocket stream and WebSocket for server_id: %s", server_id)
            try:
                ws.close()
            except Exception:
                pass

    t = threading.Thread(target=stream_pty_to_ws)
    t.daemon = True
    t.start()

    try:
        while True:
            data = ws.receive()
            if data is None:
                logger.info("Terminal WS: Received close frame/empty data from WebSocket for server_id: %s", server_id)
                break
            os.write(fd, data.encode("utf-8"))
    except Exception as e:
        logger.error("Terminal WS: Exception in WS-to-PTY stream for server_id %s: %s", server_id, e)
    finally:
        logger.info("Terminal WS: Cleaning up shell process PID: %d for server_id: %s", pid, server_id)
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            os.kill(pid, 9)
        except Exception:
            pass


# --- API Connections ---------------------------------------------------------

@app.route("/api/server/connect", methods=["POST"])
def connect_server():
    """
    SSH into a saved VPS, cache the session in the pool, and start background polling.
    """
    try:
        body      = request.get_json(force=True)
        server_id = body.get("server_id")
        password  = body.get("password")
        if not server_id or not password:
            return _json_error("Missing server_id or password")

        server = get_server_by_id(server_id)
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
            allow_agent=False,
            look_for_keys=False,
        )

        _, stdout, stderr = ssh.exec_command(_SSH_METRIC_SCRIPT)
        output = stdout.read().decode("utf-8").strip()
        err    = stderr.read().decode("utf-8").strip()

        if not output:
            ssh.close()
            logger.error("SSH script returned empty output. stderr: %s", err)
            return _json_error("No output from remote script -- check user permissions", 500)

        raw = json.loads(output)
        
        ssh_pool.add(server_id, ssh, password)
        now_ts = time.time()
        ssh_pool.update_net_stats(server_id, float(raw.get("net_rx_bytes", 0)), float(raw.get("net_tx_bytes", 0)), now_ts)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        metrics = {
            "cpu":         raw.get("cpu",       0),
            "ram":         raw.get("ram",       0),
            "disk":        raw.get("disk",      0),
            "processes":   raw.get("processes", 0),
            "uptime":      raw.get("uptime",    0),
            "network_in":  0.0,
            "network_out": 0.0,
            "server_id":   server_id,
            "timestamp":   ts,
        }

        metric_id = insert_metric(metrics)
        detector = get_detector(server_id)
        prediction = detector.predict(metrics)

        get_latest_status(server_id).update({
            "status":        "anomaly" if prediction["is_anomaly"] else "normal",
            "last_updated":  ts,
            "severity":      prediction["severity"] if prediction["is_anomaly"] else None,
            "score":         prediction["score"],
            "metric_id":     metric_id,
            "total_metrics": count_metrics(server_id),
        })

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


@app.route("/api/server/disconnect", methods=["POST"])
def disconnect_server():
    """
    Close active SSH connection and remove from connection pool.
    """
    try:
        body = request.get_json(force=True)
        server_id = body.get("server_id")
        if not server_id:
            return _json_error("Missing server_id")

        ssh_pool.remove(server_id)

        get_latest_status(server_id).update({
            "status":        "unknown",
            "last_updated":  None,
            "severity":      None,
            "score":         0.0,
            "metric_id":     None,
        })

        return jsonify({"success": True, "message": f"Server {server_id} disconnected."})
    except Exception as exc:
        logger.exception("disconnect_server error: %s", exc)
        return _json_error("Disconnect failed: " + str(exc), 500)


# --- API Docs Route ----------------------------------------------------------

@app.route("/api/docs", methods=["GET"])
def api_docs():
    """Render the API documentation page."""
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Smart Cloud Pulse — API Documentation</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <!-- Marked.js for markdown parsing -->
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <!-- Highlight.js for code block parsing -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/json.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/languages/http.min.js"></script>
    <style>
        :root {
            --bg-color: #0d1117;
            --card-bg: rgba(21, 27, 38, 0.7);
            --border-color: rgba(255, 255, 255, 0.08);
            --accent-color: #7c4dff;
            --accent-hover: #b388ff;
            --text-main: #e6edf3;
            --text-secondary: #8b949e;
            --sidebar-width: 280px;
        }
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        body {
            font-family: 'Outfit', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            display: flex;
            min-height: 100vh;
            line-height: 1.6;
        }
        /* Sidebar layout */
        aside {
            width: var(--sidebar-width);
            background: rgba(13, 17, 23, 0.95);
            border-right: 1px solid var(--border-color);
            position: fixed;
            top: 0;
            bottom: 0;
            left: 0;
            padding: 2rem 1.5rem;
            overflow-y: auto;
            z-index: 10;
        }
        .logo {
            font-weight: 700;
            font-size: 1.3rem;
            color: var(--text-main);
            display: flex;
            align-items: center;
            gap: 0.5rem;
            margin-bottom: 2.5rem;
            background: linear-gradient(135deg, #b388ff, #7c4dff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .sidebar-title {
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.1rem;
            color: var(--text-secondary);
            margin-bottom: 1rem;
            font-weight: 600;
        }
        .toc-list {
            list-style: none;
        }
        .toc-list li {
            margin-bottom: 0.5rem;
        }
        .toc-list a {
            color: var(--text-secondary);
            text-decoration: none;
            font-size: 0.95rem;
            transition: all 0.2s ease;
            display: block;
            padding: 0.25rem 0;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .toc-list a:hover {
            color: var(--accent-hover);
            transform: translateX(3px);
        }
        /* Main view */
        main {
            margin-left: var(--sidebar-width);
            flex-grow: 1;
            padding: 3rem 4rem;
            max-width: 1000px;
        }
        /* Markdown rendering styling */
        #doc-container h1 {
            font-size: 2.2rem;
            font-weight: 700;
            margin-bottom: 1rem;
            background: linear-gradient(135deg, #fff, #8b949e);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1rem;
        }
        #doc-container h2 {
            font-size: 1.6rem;
            margin-top: 3rem;
            margin-bottom: 1.5rem;
            color: #b388ff;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.5rem;
        }
        #doc-container h3 {
            font-size: 1.25rem;
            margin-top: 2rem;
            margin-bottom: 1rem;
            color: var(--text-main);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        #doc-container p {
            margin-bottom: 1.2rem;
            color: #c9d1d9;
        }
        #doc-container ul, #doc-container ol {
            margin-bottom: 1.5rem;
            padding-left: 1.5rem;
        }
        #doc-container li {
            margin-bottom: 0.5rem;
        }
        #doc-container pre {
            background-color: #161b22;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 1.2rem;
            overflow-x: auto;
            margin-bottom: 1.5rem;
            position: relative;
        }
        #doc-container code {
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            background-color: rgba(110, 118, 129, 0.2);
            padding: 0.2rem 0.4rem;
            border-radius: 4px;
        }
        #doc-container pre code {
            background-color: transparent;
            padding: 0;
            border-radius: 0;
        }
        #doc-container table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 2rem;
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
        }
        #doc-container th, #doc-container td {
            padding: 0.8rem 1rem;
            border: 1px solid var(--border-color);
            text-align: left;
        }
        #doc-container th {
            background-color: rgba(110, 118, 129, 0.1);
            font-weight: 600;
        }
        #doc-container blockquote {
            border-left: 4px solid var(--accent-color);
            background: rgba(124, 77, 255, 0.05);
            padding: 1rem 1.5rem;
            margin-bottom: 1.5rem;
            border-radius: 0 8px 8px 0;
        }
        /* Custom copy button inside code blocks */
        .copy-btn {
            position: absolute;
            top: 0.75rem;
            right: 0.75rem;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            padding: 0.35rem 0.65rem;
            border-radius: 4px;
            font-size: 0.75rem;
            cursor: pointer;
            transition: all 0.2s;
            font-family: inherit;
        }
        .copy-btn:hover {
            background: var(--accent-color);
            color: white;
        }
        /* Responsive */
        @media (max-width: 768px) {
            body {
                flex-direction: column;
            }
            aside {
                position: relative;
                width: 100%;
                border-right: none;
                border-bottom: 1px solid var(--border-color);
            }
            main {
                margin-left: 0;
                padding: 2rem 1.5rem;
            }
        }
    </style>
</head>
<body>
    <aside>
        <div class="logo">🖥️ Smart Cloud Pulse</div>
        <div class="sidebar-title">Table of Contents</div>
        <ul class="toc-list" id="toc">
            <!-- Populated dynamically -->
        </ul>
    </aside>
    <main>
        <div id="doc-container">
            <p style="color: var(--text-secondary);">Loading documentation...</p>
        </div>
    </main>

    <script>
        // Set marked options
        marked.setOptions({
            highlight: function(code, lang) {
                const language = hljs.getLanguage(lang) ? lang : 'plaintext';
                return hljs.highlight(code, { language }).value;
            },
            gfm: true,
            breaks: true
        });

        // Load the API_DOCS.md file via raw endpoint
        fetch('/api/docs/raw')
            .then(res => {
                if (!res.ok) throw new Error("Could not load API_DOCS.md");
                return res.text();
            })
            .then(markdown => {
                // Parse markdown to HTML
                document.getElementById('doc-container').innerHTML = marked.parse(markdown);
                
                // Post-process code blocks to add copy button
                document.querySelectorAll('pre').forEach(block => {
                    const btn = document.createElement('button');
                    btn.className = 'copy-btn';
                    btn.textContent = 'Copy';
                    btn.onclick = () => {
                        const code = block.querySelector('code').innerText;
                        navigator.clipboard.writeText(code);
                        btn.textContent = 'Copied!';
                        setTimeout(() => btn.textContent = 'Copy', 2000);
                    };
                    block.appendChild(btn);
                });

                // Generate TOC
                const toc = document.getElementById('toc');
                toc.innerHTML = '';
                document.getElementById('doc-container').querySelectorAll('h2, h3').forEach((header, index) => {
                    // Assign id to header for anchors
                    const headerId = 'section-' + index;
                    header.id = headerId;

                    const li = document.createElement('li');
                    const a = document.createElement('a');
                    a.href = '#' + headerId;
                    a.textContent = header.textContent;
                    if (header.tagName === 'H3') {
                        a.style.paddingLeft = '0.75rem';
                        a.style.fontSize = '0.88rem';
                    }
                    li.appendChild(a);
                    toc.appendChild(li);
                });
            })
            .catch(err => {
                document.getElementById('doc-container').innerHTML = `
                    <h1 style="color: #ff5252;">Error Loading Documentation</h1>
                    <p>${err.message}</p>
                    <p>Make sure the API_DOCS.md file is present in the project root directory.</p>
                `;
            });
    </script>
</body>
</html>
"""
    return html_content, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/docs/raw", methods=["GET"])
def raw_api_docs():
    """Retrieve raw API_DOCS.md content."""
    try:
        doc_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "API_DOCS.md")
        with open(doc_path, "r", encoding="utf-8") as f:
            return f.read(), 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as exc:
        logger.error("Could not read API_DOCS.md: %s", exc)
        return _json_error("Could not read API_DOCS.md", 404)


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
    
    # Start background polling loop as a daemon thread
    t = threading.Thread(target=background_poll_loop)
    t.daemon = True
    t.start()
    
    app.run(host=API_HOST, port=API_PORT, debug=False)
