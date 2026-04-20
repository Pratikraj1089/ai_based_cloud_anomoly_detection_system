# AI-Based Anomaly Detection System for Cloud Resource Monitoring

> **Organization:** NTPL Digital Private Limited, Noida  
> **Group:** CU — MCA — Group-4  
> **Stack:** Python · Flask · Isolation Forest · Chart.js · SQLite

---

## 📌 Overview

A full-stack, production-grade AI system that monitors cloud/VPS server resources in real-time, detects unusual patterns using **Isolation Forest** (scikit-learn), and alerts operators before failures occur.

```
┌─────────────────────────────────────────────────────────────────────┐
│  VPS Agent (monitor.py)                                             │
│  psutil → JSON → POST /api/metrics ──────────────────────────────┐ │
└─────────────────────────────────────────────────────────────────  │ │
                                                                    ▼ │
                                               Flask API (app.py)    │
                                               ┌───────────────────┐  │
                                               │  SQLite DB        │  │
                                               │  Isolation Forest │  │
                                               │  Alert System     │  │
                                               └───────────────────┘  │
                                                        │              │
                                               Dashboard (index.html)  │
                                               Chart.js Live Graphs ◄──┘
```

---

## 🗂️ Project Structure

```
anomaly-detection/
├── agent/
│   └── monitor.py          ← VPS agent (psutil + schedule)
├── backend/
│   ├── app.py              ← Flask REST API (6 endpoints)
│   ├── model.py            ← Isolation Forest ML model
│   ├── database.py         ← SQLite handler (3 tables)
│   ├── alerts.py           ← Email + webhook + console alerts
│   └── __init__.py
├── dashboard/
│   ├── index.html          ← Dark-theme live dashboard
│   ├── style.css           ← Glassmorphism CSS design
│   └── app.js              ← Chart.js live graphs + API polling
├── config.py               ← All settings (no hardcoded values)
├── requirements.txt
└── README.md
```

---

## ⚙️ Setup Instructions

### Prerequisites

- Python 3.10+
- `pip`
- A Linux/macOS VPS or local machine with internet access

---

### Step 1 — Clone / Navigate to the Project

```bash
cd /path/to/mca_cloud_anomaly_detection
```

---

### Step 2 — Create & Activate a Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate          # Linux / macOS
# venv\Scripts\activate           # Windows
```

---

### Step 3 — Install Dependencies

```bash
pip install -r requirements.txt
```

---

### Step 4 — Configure Settings (Optional)

All settings live in **`config.py`**. Override with environment variables:

| Variable | Default | Description |
|---|---|---|
| `SERVER_NAME` | `VPS-Server-01` | Display name for your server |
| `SERVER_IP`   | `127.0.0.1`     | IP shown in the dashboard |
| `API_PORT`    | `5000`          | Flask API port |
| `API_KEY`     | `mca-group4-secret-key` | Shared secret agent ↔ API |
| `AGENT_INTERVAL` | `30` | Seconds between metric sends |
| `MODEL_TRAIN_SAMPLES` | `200` | Readings before model trains |
| `ALERT_EMAIL_ENABLED` | `false` | Enable email alerts |

Example — override via env:
```bash
export SERVER_NAME="MyVPS"
export SERVER_IP="203.0.113.5"
export API_KEY="my-very-secret-key"
```

Or edit `config.py` directly for permanent changes.

---

### Step 5 — Start the Flask API

Open **Terminal 1**:

```bash
# From project root
python -m backend.app
```

Expected output:
```
INFO  Starting Flask API on 0.0.0.0:5000
INFO  Database initialised at backend/anomaly_detection.db
```

The API and dashboard are available at: **http://localhost:5000**

---

### Step 6 — Start the VPS Agent

Open **Terminal 2** (same machine or a remote VPS):

```bash
# From project root
python agent/monitor.py
```

Expected output:
```
INFO  === VPS Monitor Agent starting — Server: VPS-Server-01 (127.0.0.1) ===
INFO  Sent | CPU=12.3% RAM=54.1% Disk=38.0% | normal (score=0.0423)
```

> **Remote VPS?** Copy the project to the VPS and set `API_BASE_URL` to point to your API server's public IP/domain:
> ```bash
> export API_BASE_URL="http://YOUR_API_IP:5000"
> python agent/monitor.py
> ```

---

### Step 7 — View the Dashboard

Open your browser and navigate to:
```
http://localhost:5000
```

The dashboard auto-refreshes every **10 seconds**.

---

## 🤖 AI Model Details

| Parameter | Value |
|---|---|
| Algorithm | Isolation Forest |
| Library | scikit-learn 1.4 |
| Normalisation | StandardScaler |
| Contamination | 0.05 (5%) |
| Training samples | 200 readings |
| Persistence | `joblib` (`.joblib` files) |

### Anomaly Scoring

| Score Range | Severity |
|---|---|
| ≥ -0.1 | ✅ Normal |
| < -0.1 | 🟡 Low |
| < -0.3 | 🟠 Medium |
| < -0.5 | 🔴 High |

### Features Used

`cpu` · `ram` · `disk` · `network_in` · `network_out` · `processes`

---

## 🔌 API Endpoints

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/metrics` | ✅ Key | Agent sends metrics |
| `GET`  | `/api/metrics/latest` | ❌ | Dashboard polls metrics |
| `GET`  | `/api/anomalies` | ❌ | Dashboard polls anomalies |
| `GET`  | `/api/status` | ❌ | Overall server status |
| `POST` | `/api/train` | ✅ Key | Trigger model retrain |
| `GET`  | `/api/alerts` | ❌ | Alert history |
| `GET`  | `/api/health` | ❌ | Health check / ping |

**X-API-Key header** is required for authenticated endpoints.

### Example — Send Metrics Manually

```bash
curl -X POST http://localhost:5000/api/metrics \
  -H "Content-Type: application/json" \
  -H "X-API-Key: mca-group4-secret-key" \
  -d '{"cpu":85.5,"ram":92.1,"disk":74.0,"network_in":1024,"network_out":512,"processes":220,"uptime":86400}'
```

---

## 📧 Alert Configuration

### Email Alerts

Set these environment variables before starting the API:

```bash
export ALERT_EMAIL_ENABLED=true
export ALERT_EMAIL_FROM=alerts@yourcompany.com
export ALERT_EMAIL_TO=admin@yourcompany.com
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=your_gmail@gmail.com
export SMTP_PASSWORD=your_app_password
```

> Use a **Gmail App Password** (not your main password) — generate at [Google Account Security](https://myaccount.google.com/security).

### Webhook Alerts (Slack, Teams, custom)

```bash
export ALERT_WEBHOOK_ENABLED=true
export ALERT_WEBHOOK_URL=https://hooks.slack.com/services/XXX/YYY/ZZZ
```

---

## 🗄️ Database Schema

```sql
-- Metric readings from the agent
CREATE TABLE metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,
    cpu          REAL    NOT NULL,
    ram          REAL    NOT NULL,
    disk         REAL    NOT NULL,
    network_in   REAL    NOT NULL DEFAULT 0,
    network_out  REAL    NOT NULL DEFAULT 0,
    processes    INTEGER NOT NULL DEFAULT 0,
    uptime       REAL    NOT NULL DEFAULT 0
);

-- Detected anomalies
CREATE TABLE anomalies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT    NOT NULL,
    metric_values TEXT    NOT NULL,  -- JSON snapshot
    anomaly_score REAL    NOT NULL,
    severity      TEXT    NOT NULL   -- low | medium | high
);

-- Alert delivery log
CREATE TABLE alerts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT    NOT NULL,
    message   TEXT    NOT NULL,
    status    TEXT    NOT NULL  -- sent | pending | failed
);
```

---

## ☁️ Cloud Provider Roadmap

| Provider | Status | Method |
|---|---|---|
| **VPS / Local** | ✅ **Active** | psutil agent |
| **AWS EC2** | 🔜 Coming Soon | boto3 + CloudWatch |
| **GCP Compute** | 🔜 Coming Soon | google-cloud-monitoring |
| **Azure VM** | 🔜 Coming Soon | azure-mgmt-monitor |

Click the provider buttons on the dashboard to see setup instructions for each.

---

## 🚀 Running in Production

### Background with `nohup`

```bash
# Start API in background
nohup python -m backend.app > logs/api.log 2>&1 &

# Start Agent in background
nohup python agent/monitor.py > logs/agent.log 2>&1 &
```

### systemd Service (Linux)

```ini
# /etc/systemd/system/anomaly-api.service
[Unit]
Description=Anomaly Detection API
After=network.target

[Service]
WorkingDirectory=/opt/anomaly-detection
ExecStart=/opt/anomaly-detection/venv/bin/python -m backend.app
Restart=on-failure
User=ubuntu

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now anomaly-api
```

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError` | Activate venv: `source venv/bin/activate` |
| `ConnectionRefusedError` in agent | Start Flask API first |
| Dashboard shows "Cannot reach API" | Check API is running on port 5000 |
| Model not predicting yet | Wait for 200 readings (shown in dashboard progress bar) |
| Email not sending | Use Gmail App Password, not account password |

---

## 👥 Team

| Role | Name |
|---|---|
| Project Lead | CU MCA Group-4 |
| Organization | NTPL Digital Private Limited, Noida |
| Academic | Chandigarh University — MCA Programme |

---

*Built with Python · Flask · scikit-learn · Chart.js · SQLite*
