/*
============================================================
app.js — Dashboard Logic & Live Charts
Project : AI-Based Anomaly Detection System for Cloud Resource Monitoring
Org     : NTPL Digital Private Limited, Noida
Group   : CU - MCA - Group-4
------------------------------------------------------------
Responsibilities:
  - Poll API every 10 seconds for latest metrics & anomalies
  - Render / update Chart.js line graphs for CPU, RAM, Disk, Net
  - Update status bar, stat cards, anomaly table
  - Handle cloud provider modal display
  - Trigger model retrain via API
============================================================
*/

"use strict";

// ─── Configuration ────────────────────────────────────────────────────────────
const CONFIG = {
    apiBase: "", // same origin — Flask serves the dashboard
    pollInterval: 5_000, // ms
    maxPoints: 100,
    apiKey: "mca-group4-secret-key",
};

// ─── State ────────────────────────────────────────────────────────────────────
const state = {
    metrics: [],
    anomalies: [],
    status: null,
    polling: null,
};

// ─── Chart Instances ──────────────────────────────────────────────────────────
const charts = {};

// ─── Chart.js Default Settings ───────────────────────────────────────────────
const chartDefaults = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 400 },
    plugins: {
        legend: { display: false },
        tooltip: {
            backgroundColor: "rgba(15,22,41,0.95)",
            borderColor: "rgba(255,255,255,0.08)",
            borderWidth: 1,
            titleColor: "#94a3b8",
            bodyColor: "#f1f5f9",
            padding: 10,
            cornerRadius: 8,
        },
    },
    scales: {
        x: {
            grid: { color: "rgba(255,255,255,0.04)", drawBorder: false },
            ticks: { color: "#475569", maxTicksLimit: 6, font: { size: 10 } },
        },
        y: {
            grid: { color: "rgba(255,255,255,0.04)", drawBorder: false },
            ticks: { color: "#475569", font: { size: 10 } },
            min: 0,
        },
    },
};

// ─── Colour palette for each chart ────────────────────────────────────────────
const chartThemes = {
    cpu: { stroke: "#3b82f6", fill: "rgba(59,130,246,0.12)" },
    ram: { stroke: "#8b5cf6", fill: "rgba(139,92,246,0.12)" },
    disk: { stroke: "#06b6d4", fill: "rgba(6,182,212,0.12)" },
    net_in: { stroke: "#10b981", fill: "rgba(16,185,129,0.12)" },
    net_out: { stroke: "#f59e0b", fill: "rgba(245,158,11,0.12)" },
    procs: { stroke: "#f97316", fill: "rgba(249,115,22,0.12)" },
};

// ─── DOM Helpers ────────────────────────────────────────────────────────────
const el = (id) => document.getElementById(id);
const qs = (sel) => document.querySelector(sel);
const qsa = (sel) => document.querySelectorAll(sel);

// ─── Toast Notifications ─────────────────────────────────────────────────────
function showToast(msg, type = "success") {
    const container = el("toastContainer");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = msg;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4200);
}

// ─── API Wrapper ─────────────────────────────────────────────────────────────
async function apiFetch(path, options = {}) {
    const resp = await fetch(`${CONFIG.apiBase}${path}`, {
        headers: { "X-API-Key": CONFIG.apiKey, ...options.headers },
        ...options,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
        throw new Error(data.error || `HTTP ${resp.status}`);
    }
    return data;
}

// ─── Chart Initialisation ─────────────────────────────────────────────────────
function initChart(id, theme, yMax, yLabel) {
    const ctx = el(id).getContext("2d");
    const cfg = JSON.parse(JSON.stringify(chartDefaults));          // deep copy
    cfg.scales.y.max = yMax;
    cfg.scales.y.ticks.callback = (v) => `${v}${yLabel}`;

    charts[id] = new Chart(ctx, {
        type: "line",
        data: {
            labels: [],
            datasets: [{
                data: [],
                borderColor: theme.stroke,
                backgroundColor: theme.fill,
                fill: true,
                tension: 0.4,
                borderWidth: 2,
                pointRadius: 0,
                pointHoverRadius: 4,
                pointHoverBackgroundColor: theme.stroke,
            }],
        },
        options: cfg,
    });
}

function initAllCharts() {
    initChart("chartCPU", chartThemes.cpu, 100, "%");
    initChart("chartRAM", chartThemes.ram, 100, "%");
    initChart("chartDisk", chartThemes.disk, 100, "%");
    initChart("chartNetIn", chartThemes.net_in, undefined, " B/s");
    initChart("chartNetOut", chartThemes.net_out, undefined, " B/s");
    initChart("chartProcs", chartThemes.procs, undefined, "");
}

// ─── Chart Update ─────────────────────────────────────────────────────────────
function pushToChart(chartId, label, value) {
    const ch = charts[chartId];
    if (!ch) return;
    ch.data.labels.push(label);
    ch.data.datasets[0].data.push(value);
    if (ch.data.labels.length > CONFIG.maxPoints) {
        ch.data.labels.shift();
        ch.data.datasets[0].data.shift();
    }
    ch.update("none"); // skip animation for live updates
}

function rebuildCharts(metrics) {
    // Clear charts
    Object.values(charts).forEach((ch) => {
        ch.data.labels = [];
        ch.data.datasets[0].data = [];
    });
    metrics.forEach((m) => {
        const label = formatTime(m.timestamp);
        pushToChart("chartCPU", label, m.cpu);
        pushToChart("chartRAM", label, m.ram);
        pushToChart("chartDisk", label, m.disk);
        pushToChart("chartNetIn", label, m.network_in);
        pushToChart("chartNetOut", label, m.network_out);
        pushToChart("chartProcs", label, m.processes);
    });
    Object.values(charts).forEach((ch) => ch.update());
}

// ─── Circular Ring Update ─────────────────────────────────────────────────────
function updateRing(svgId, value, maxVal = 100) {
    const ring = el(svgId);
    if (!ring) return;
    const r = ring.r.baseVal.value;
    const circumf = 2 * Math.PI * r;
    const pct = Math.min(value / maxVal, 1);
    ring.style.strokeDasharray = circumf;
    ring.style.strokeDashoffset = circumf * (1 - pct);
}

// ─── Stat Cards ───────────────────────────────────────────────────────────────
function updateStatCards(metric) {
    if (!metric) return;

    const map = {
        "valCPU": { v: metric.cpu, unit: "%" },
        "valRAM": { v: metric.ram, unit: "%" },
        "valDisk": { v: metric.disk, unit: "%" },
        "valNetIn": { v: formatBytes(metric.network_in), unit: "" },
        "valNetOut": { v: formatBytes(metric.network_out), unit: "" },
        "valProcs": { v: metric.processes, unit: "" },
        "valUptime": { v: formatUptime(metric.uptime), unit: "" },
    };

    Object.entries(map).forEach(([id, { v, unit }]) => {
        const elem = el(id);
        if (elem) elem.textContent = v + unit;
    });

    updateRing("ringCPU", metric.cpu);
    updateRing("ringRAM", metric.ram);
    updateRing("ringDisk", metric.disk);

    // Update per-chart live value labels
    el("liveCPU") && (el("liveCPU").textContent = metric.cpu.toFixed(1) + "%");
    el("liveRAM") && (el("liveRAM").textContent = metric.ram.toFixed(1) + "%");
    el("liveDisk") && (el("liveDisk").textContent = metric.disk.toFixed(1) + "%");
    el("liveNetIn") && (el("liveNetIn").textContent = formatBytes(metric.network_in));
    el("liveNetOut") && (el("liveNetOut").textContent = formatBytes(metric.network_out));
    el("liveProcs") && (el("liveProcs").textContent = metric.processes);
}

// ─── Status Badge ─────────────────────────────────────────────────────────────
function updateStatusBadge(statusData) {
    const badge = el("statusBadge");
    const dot = el("statusDot");
    const text = el("statusText");
    const ts = el("lastUpdated");

    if (!statusData || !badge) return;

    const s = (statusData.status || "normal").toLowerCase();
    badge.className = `status-badge ${s}`;
    text.textContent = s.charAt(0).toUpperCase() + s.slice(1);

    if (ts && statusData.last_updated)
        ts.textContent = "Updated: " + formatTime(statusData.last_updated);

    // Flash body on anomaly
    if (s === "anomaly") {
        document.body.classList.add("anomaly-flash");
        setTimeout(() => document.body.classList.remove("anomaly-flash"), 1200);
    }
}

// ─── Anomaly Table ────────────────────────────────────────────────────────────
function renderAnomalyTable(anomalies) {
    const tbody = el("anomalyTbody");
    if (!tbody) return;

    if (!anomalies || !anomalies.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="no-data">✅ No anomalies detected yet</td></tr>`;
        el("anomalyCount") && (el("anomalyCount").textContent = "0");
        return;
    }

    el("anomalyCount") && (el("anomalyCount").textContent = anomalies.length);

    tbody.innerHTML = anomalies.slice(0, 30).map((a) => {
        const vals = safeParseJSON(a.metric_values);
        const badgeClass = String(a.severity || "normal").toLowerCase().replace(/\s+/g, "-");
        return `
      <tr>
        <td>${formatTime(a.timestamp)}</td>
        <td>${vals ? vals.cpu?.toFixed(1) + "%" : "—"}</td>
        <td>${vals ? vals.ram?.toFixed(1) + "%" : "—"}</td>
        <td>${a.anomaly_score?.toFixed(4)}</td>
        <td><span class="sev-badge ${badgeClass}">${a.severity}</span></td>
      </tr>`;
    }).join("");
}

// ─── Model Info Panel ─────────────────────────────────────────────────────────
function renderModelPanel(status) {
    if (!status) return;
    const model = status.model || {};
    const total = status.total_metrics || 0;
    const trainSamples = model.train_samples_required || 20;
    const pct = Math.min((total / trainSamples) * 100, 100);

    el("modelTrained") && (el("modelTrained").textContent = model.is_trained ? "✅ Yes" : "❌ No (collecting data)");
    el("modelSamples") && (el("modelSamples").textContent = `${total} / ${trainSamples}`);
    el("trainProgress") && (el("trainProgress").style.width = pct.toFixed(1) + "%");
    el("contamination") && (el("contamination").textContent = (model.contamination ?? 0.05).toFixed(2));
    el("totalMetrics") && (el("totalMetrics").textContent = total);
}

// ─── Fetch & Refresh ─────────────────────────────────────────────────────────
async function fetchAll() {
    try {
        const sid = state.currentServerId || 0;
        const [metricRes, anomalyRes, statusRes] = await Promise.all([
            apiFetch(`/api/metrics/latest?limit=100${sid !== 0 ? `&server_id=${sid}` : ""}`),
            apiFetch(`/api/anomalies?limit=50${sid !== 0 ? `&server_id=${sid}` : ""}`),
            apiFetch(`/api/status?server_id=${sid}`),
        ]);

        if (state.currentServerId !== sid) return;

        const metrics = metricRes.data || [];
        const anomalies = anomalyRes.data || [];
        const statusD = statusRes.data || {};

        // First load or server switch: rebuild charts; subsequent: incremental
        if (state.metrics.length === 0 || metrics.length < state.metrics.length) {
            rebuildCharts(metrics);
        } else if (metrics.length > state.metrics.length) {
            const newOnes = metrics.slice(state.metrics.length);
            newOnes.forEach((m) => {
                const label = formatTime(m.timestamp);
                pushToChart("chartCPU", label, m.cpu);
                pushToChart("chartRAM", label, m.ram);
                pushToChart("chartDisk", label, m.disk);
                pushToChart("chartNetIn", label, m.network_in);
                pushToChart("chartNetOut", label, m.network_out);
                pushToChart("chartProcs", label, m.processes);
            });
        }

        state.metrics = metrics;
        state.anomalies = anomalies;
        state.status = statusD;

        const latest = metrics[metrics.length - 1];
        if (latest) {
            updateStatCards(latest);
        } else {
            clearStatCards();
        }
        
        updateStatusBadge(statusD);
        renderAnomalyTable(anomalies);
        renderModelPanel(statusD);
        fetchEmailStatus();

        el("apiErrorBanner") && (el("apiErrorBanner").style.display = "none");

    } catch (err) {
        console.warn("Poll failed:", err);
        const banner = el("apiErrorBanner");
        if (banner) banner.style.display = "block";
    }
}

// ─── Email Notification Status ────────────────────────────────────────────────
async function fetchEmailStatus() {
    try {
        const res = await apiFetch("/api/email-status");
        if (res) {
            const enabledEl = el("emailEnabledBadge");
            if (enabledEl) {
                enabledEl.textContent = res.email_enabled ? "✅ Enabled" : "❌ Disabled";
                enabledEl.style.color = res.email_enabled ? "#2ecc71" : "#e74c3c";
            }
            
            const connEl = el("smtpConnectionBadge");
            if (connEl) {
                connEl.textContent = res.smtp_connection ? "✅ Connected" : "❌ Disconnected";
                connEl.style.color = res.smtp_connection ? "#2ecc71" : "#e74c3c";
            }

            const authEl = el("smtpAuthBadge");
            if (authEl) {
                authEl.textContent = res.authentication ? "✅ Verified" : "❌ Failed / Unverified";
                authEl.style.color = res.authentication ? "#2ecc71" : "#e74c3c";
            }

            el("lastEmailSent") && (el("lastEmailSent").textContent = res.last_email_sent ? formatTime(res.last_email_sent) : "Never");
            el("emailsSentToday") && (el("emailsSentToday").textContent = res.sent_today);
            el("emailsFailedToday") && (el("emailsFailedToday").textContent = res.failed_today);
            el("smtpStatusMsg") && (el("smtpStatusMsg").textContent = res.smtp_status_message || "No status message available");
        }
    } catch (err) {
        console.warn("Failed to fetch email status:", err);
    }
}

async function triggerTestEmail() {
    const btn = el("testEmailBtn");
    if (!btn) return;
    btn.disabled = true;
    btn.innerHTML = '✉️ Sending Test...';
    try {
        const res = await apiFetch("/api/test-email", { method: "POST" });
        if (res && res.email_sent) {
            showToast("Test alert email sent successfully! ✅", "success");
        } else {
            showToast("Failed to send test email: " + (res.message || "Unknown error") + " ❌", "error");
        }
    } catch (err) {
        showToast("Error sending test email: " + err.message + " ❌", "error");
    } finally {
        btn.disabled = false;
        btn.innerHTML = '✉️ Send Test Email';
        fetchEmailStatus();
    }
}

// ─── Manual Retrain ───────────────────────────────────────────────────────────
async function triggerRetrain() {
    const btn = el("retrainBtn");
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> Training…`;
    try {
        const sid = state.currentServerId || 0;
        const res = await apiFetch(`/api/train?server_id=${sid}`, {
            method: "POST",
            headers: { "X-API-Key": CONFIG.apiKey },
        });
        if (res.success) {
            showToast(`Model retrained on ${res.samples} samples! ✅`, "success");
            await fetchAll();
        } else {
            showToast("Training failed: " + (res.error || "unknown"), "error");
        }
    } catch (err) {
        showToast("Retrain error: " + err.message, "error");
    } finally {
        btn.disabled = false;
        btn.innerHTML = `🔄 Retrain Model`;
    }
}

// ─── Cloud Provider Modals ────────────────────────────────────────────────────
const CLOUD_MODALS = {
    aws: {
        title: "☁️ AWS EC2 — Connect via CloudWatch",
        body: `
      <div class="coming-soon-tag">⏳ Coming Soon</div>
      <p>To monitor AWS EC2 instances, this system will integrate with <strong>Amazon CloudWatch</strong> using the <code>boto3</code> SDK.</p>
      <p><strong>Required setup steps:</strong></p>
      <pre>
# 1. Install AWS SDK
pip install boto3

# 2. Configure AWS credentials
aws configure
# Enter: Access Key, Secret Key, Region, Output format

# 3. Fetch CloudWatch metrics (sample)
import boto3
client = boto3.client('cloudwatch', region_name='us-east-1')
response = client.get_metric_statistics(
    Namespace='AWS/EC2',
    MetricName='CPUUtilization',
    Dimensions=[{'Name': 'InstanceId', 'Value': 'i-XXXXXXXX'}],
    StartTime=datetime.utcnow() - timedelta(minutes=5),
    EndTime=datetime.utcnow(),
    Period=300,
    Statistics=['Average']
)</pre>
      <p style="color:var(--text-muted);font-size:0.78rem;">This endpoint will be available in a future release. The anomaly detection pipeline remains identical once the metrics are piped in.</p>
    `,
    },
    gcp: {
        title: "🌍 GCP Compute Engine — Connect via Cloud Monitoring",
        body: `
      <div class="coming-soon-tag">⏳ Coming Soon</div>
      <p>GCP integration uses the <strong>Google Cloud Monitoring SDK</strong> to pull Compute Engine instance metrics.</p>
      <p><strong>Required setup steps:</strong></p>
      <pre>
# 1. Install SDK
pip install google-cloud-monitoring

# 2. Authenticate
gcloud auth application-default login

# 3. Fetch metrics (sample)
from google.cloud import monitoring_v3
client = monitoring_v3.MetricServiceClient()
project = 'projects/YOUR_PROJECT_ID'
interval = monitoring_v3.TimeInterval({...})
result = client.list_time_series(
    request={
        "name": project,
        "filter": 'metric.type="compute.googleapis.com/instance/cpu/utilization"',
        "interval": interval,
        "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
    }
)</pre>
      <p style="color:var(--text-muted);font-size:0.78rem;">Coming in a future release. The same Isolation Forest model will process GCP metrics without modification.</p>
    `,
    },
    azure: {
        title: "🔷 Azure VM — Connect via Azure Monitor",
        body: `
      <div class="coming-soon-tag">⏳ Coming Soon</div>
      <p>Azure integration leverages <strong>Azure Monitor Metrics</strong> with the <code>azure-mgmt-monitor</code> SDK.</p>
      <p><strong>Required setup steps:</strong></p>
      <pre>
# 1. Install SDK
pip install azure-mgmt-monitor azure-identity

# 2. Authenticate
from azure.identity import DefaultAzureCredential
from azure.mgmt.monitor import MonitorManagementClient

credential = DefaultAzureCredential()
client = MonitorManagementClient(credential, "SUBSCRIPTION_ID")

# 3. Fetch CPU metrics
metrics_data = client.metrics.list(
    resource_uri='/subscriptions/SUB/resourceGroups/RG/providers/'
                 'Microsoft.Compute/virtualMachines/VM_NAME',
    metricnames='Percentage CPU',
    interval='PT1M',
    aggregation='Average'
)</pre>
      <p style="color:var(--text-muted);font-size:0.78rem;">This connector will be available in an upcoming release alongside GCP support.</p>
    `,
    },
};

function openModal(provider) {
    const m = CLOUD_MODALS[provider];
    if (!m) return;
    el("modalTitle").textContent = m.title;
    el("modalBody").innerHTML = m.body;
    el("cloudModal").classList.add("open");
}
function closeModal() {
    el("cloudModal").classList.remove("open");
}

// ─── Utility Formatters ───────────────────────────────────────────────────────
function formatTime(ts) {
    if (!ts) return "";
    try {
        const d = new Date(ts.endsWith("Z") ? ts : ts + "Z");
        return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch { return ts; }
}

function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) return "—";
    const b = Number(bytes);
    if (b < 1024) return b.toFixed(0) + " B/s";
    if (b < 1024 * 1024) return (b / 1024).toFixed(1) + " KB/s";
    return (b / 1024 / 1024).toFixed(2) + " MB/s";
}

function formatUptime(seconds) {
    if (!seconds) return "—";
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return `${d}d ${h}h ${m}m`;
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
}

function safeParseJSON(str) {
    try { return JSON.parse(str); } catch { return null; }
}

// ─── State Switcher & Local Storage ──────────────────────────────────────────

let storedServers = [];
state.currentServerId = 0; // default to local PC

async function loadServers() {
    try {
        const res = await apiFetch("/api/servers");
        if (res.success && res.data) {
            storedServers = res.data;
            renderServerList();
        }
    } catch (err) { console.warn("Failed to load servers", err); }
}

function renderServerList() {
    const container = el("serverListContainer");
    if (!container) return;

    document.querySelectorAll(".dyn-server-btn").forEach(btn => btn.remove());

    const addBtn = el("btnAddServer");
    storedServers.forEach(srv => {
        const btn = document.createElement("button");
        const isActive = state.currentServerId === srv.id;
        btn.className = "cloud-btn dyn-server-btn" + (isActive ? " active" : "");
        btn.id = "srvBtn" + srv.id;
        btn.title = srv.ip + ":" + (srv.port || 22);
        btn.innerHTML = `<span class="cloud-icon">🖥️</span> ${srv.name}` +
            (isActive ? ` <span class="badge">Live</span>` : "");
        
        btn.onclick = async () => {
            try {
                const statusRes = await apiFetch(`/api/status?server_id=${srv.id}`);
                const statusD = statusRes.data || {};
                
                if (statusD.status === "unknown" || statusD.status === "starting") {
                    el("connServerId").value = srv.id;
                    el("connServerName").textContent = `${srv.name}  (${srv.ip})`;
                    el("connPassword").value = "";
                    el("passwordModal").classList.add("open");
                    setTimeout(() => el("connPassword")?.focus(), 100);
                } else {
                    if (state.currentServerId === srv.id) {
                        showToast(`Already viewing ${srv.name}`, "warning");
                        return;
                    }
                    switchServer(srv.id);
                }
            } catch (err) {
                el("connServerId").value = srv.id;
                el("connServerName").textContent = `${srv.name}  (${srv.ip})`;
                el("connPassword").value = "";
                el("passwordModal").classList.add("open");
                setTimeout(() => el("connPassword")?.focus(), 100);
            }
        };
        container.insertBefore(btn, addBtn);
    });
}

async function switchServer(serverId) {
    serverId = parseInt(serverId);
    state.currentServerId = serverId;
    
    const tabTerm = el("tabTerminal");
    const btnDisc = el("btnDisconnectServer");
    
    if (serverId === 0) {
        if (tabTerm) tabTerm.style.display = "none";
        if (btnDisc) btnDisc.style.display = "none";
        switchView("dashboard");
    } else {
        if (tabTerm) tabTerm.style.display = "inline-block";
        if (btnDisc) btnDisc.style.display = "inline-block";
    }
    
    // Highlight active VPS button
    qsa(".dyn-server-btn").forEach(btn => {
        btn.classList.remove("active");
        if (btn.id === `srvBtn${serverId}`) {
            btn.classList.add("active");
        }
    });
    
    const localBtn = el("btnVPS");
    if (localBtn) {
        if (serverId === 0) localBtn.classList.add("active");
        else localBtn.classList.remove("active");
    }
    
    state.metrics = [];
    state.anomalies = [];
    
    await fetchAll();
    resetTerminal();
}

async function disconnectActiveServer() {
    const sid = state.currentServerId;
    if (sid === 0) return;
    
    try {
        const res = await apiFetch("/api/server/disconnect", {
            method: "POST",
            body: JSON.stringify({ server_id: sid }),
        });
        if (res.success) {
            showToast(`Server disconnected.`, "success");
            switchServer(0);
        } else {
            showToast(res.error || "Disconnect failed", "error");
        }
    } catch (err) {
        showToast("Disconnect error: " + err.message, "error");
    }
}

// ─── WebSocket Metrics Broadcaster Client ─────────────────────────────────────

let metricsWS = null;
function initMetricsWebSocket() {
    if (metricsWS) {
        try { metricsWS.close(); } catch(_) {}
    }
    
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}/ws/metrics`;
    
    metricsWS = new WebSocket(wsUrl);
    
    metricsWS.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            if (parseInt(data.server_id) === state.currentServerId) {
                const m = data.metrics;
                const label = formatTime(m.timestamp);
                
                state.metrics.push(m);
                if (state.metrics.length > CONFIG.maxPoints) {
                    state.metrics.shift();
                }
                
                pushToChart("chartCPU", label, m.cpu);
                pushToChart("chartRAM", label, m.ram);
                pushToChart("chartDisk", label, m.disk);
                pushToChart("chartNetIn", label, m.network_in);
                pushToChart("chartNetOut", label, m.network_out);
                pushToChart("chartProcs", label, m.processes);
                Object.values(charts).forEach(ch => ch.update("none"));
                
                updateStatCards(m);
                fetchAll(); // Sync remaining database components (anomalies list, etc.)
            }
        } catch(e) {
            console.error("WS metrics handling error:", e);
        }
    };
    
    metricsWS.onclose = () => {
        setTimeout(initMetricsWebSocket, 5000);
    };
}

// ─── xterm.js Web Terminal Logic ──────────────────────────────────────────────

let terminalObj = null;
let terminalWS = null;
let terminalResizeListener = null;

function resetTerminal() {
    if (terminalResizeListener) {
        window.removeEventListener("resize", terminalResizeListener);
        terminalResizeListener = null;
    }
    if (terminalWS) {
        try { terminalWS.close(); } catch(_) {}
        terminalWS = null;
    }
    const termDiv = el("terminal");
    if (termDiv) termDiv.innerHTML = "";
    terminalObj = null;
}

function initTerminal() {
    if (state.currentServerId === 0) return;
    
    // Always start with a clean session to prevent stuck dead terminal states
    resetTerminal();
    
    const termDiv = el("terminal");
    if (!termDiv) return;
    
    terminalObj = new Terminal({
        cursorBlink: true,
        fontSize: 14,
        fontFamily: "'JetBrains Mono', monospace",
        theme: {
            background: "#0b0f19",
            foreground: "#f1f5f9",
            cursor: "#3b82f6",
        }
    });
    
    const fitAddon = new FitAddon.FitAddon();
    terminalObj.loadAddon(fitAddon);
    
    terminalObj.open(termDiv);
    
    // Use a small delay to make sure container layout is complete before fitting
    setTimeout(() => {
        try { fitAddon.fit(); } catch(e) { console.error("xterm fit error", e); }
    }, 150);
    
    terminalResizeListener = () => {
        try { fitAddon.fit(); } catch(_) {}
    };
    window.addEventListener("resize", terminalResizeListener);
    
    terminalObj.write("Connecting to remote shell session...\r\n");
    
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}/ws/terminal?server_id=${state.currentServerId}`;
    
    terminalWS = new WebSocket(wsUrl);
    
    terminalWS.onopen = () => {
        terminalObj.write("Connected to shell! Initializing interactive SSH session...\r\n\r\n");
    };
    
    terminalWS.onmessage = (event) => {
        terminalObj.write(event.data);
    };
    
    terminalWS.onclose = () => {
        terminalObj.write("\r\nSession closed by remote server.\r\n");
    };
    
    terminalWS.onerror = (err) => {
        terminalObj.write(`\r\nConnection error: ${err.message}\r\n`);
    };
    
    terminalObj.onData((data) => {
        if (terminalWS && terminalWS.readyState === WebSocket.OPEN) {
            terminalWS.send(data);
        }
    });
}

function switchView(viewName) {
    const tabDash = el("tabDashboard");
    const tabTerm = el("tabTerminal");
    const dashView = el("dashboardView");
    const termCont = el("terminalContainer");
    
    if (viewName === "dashboard") {
        if (dashView) dashView.style.display = "block";
        if (termCont) termCont.style.display = "none";
        
        tabDash.classList.add("active");
        tabTerm.classList.remove("active");
    } else if (viewName === "terminal") {
        if (dashView) dashView.style.display = "none";
        if (termCont) termCont.style.display = "block";
        
        tabDash.classList.remove("active");
        tabTerm.classList.add("active");
        
        initTerminal();
    }
}

function clearStatCards() {
    const ids = ["valCPU", "valRAM", "valDisk", "valNetIn", "valNetOut", "valProcs", "valUptime", "liveCPU", "liveRAM", "liveDisk", "liveNetIn", "liveNetOut", "liveProcs"];
    ids.forEach(id => {
        const elem = el(id);
        if (elem) elem.textContent = "—";
    });
    updateRing("ringCPU", 0);
    updateRing("ringRAM", 0);
    updateRing("ringDisk", 0);
}

// ─── Form & Event Handlers ────────────────────────────────────────────────────

function handleAddServerSubmit(e) {
    e.preventDefault();
    const btn = el("addServerSubmitBtn");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Saving...';

    const payload = {
        name:     el("asName").value.trim(),
        ip:       el("asIP").value.trim(),
        port:     parseInt(el("asPort").value || "22"),
        username: el("asUser").value.trim(),
    };

    apiFetch("/api/servers", { method: "POST", body: JSON.stringify(payload) })
        .then(res => {
            if (res.success) {
                showToast(`Server '${payload.name}' added!`);
                el("addServerModal").classList.remove("open");
                el("addServerForm").reset();
                loadServers();
            } else {
                showToast(res.error || "Failed to add server", "error");
            }
        })
        .catch(err => showToast("Error: " + err.message, "error"))
        .finally(() => { btn.disabled = false; btn.innerHTML = "Save Server"; });
}

function handlePasswordSubmit(e) {
    e.preventDefault();
    const btn      = el("passwordSubmitBtn");
    const serverId = parseInt(el("connServerId").value);
    const password = el("connPassword").value;

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Connecting...';

    apiFetch("/api/server/connect", {
        method: "POST",
        body: JSON.stringify({ server_id: serverId, password }),
    })
    .then(res => {
        if (res.success) {
            el("passwordModal").classList.remove("open");
            showToast(`Connected to ${res.server_name}! Persistent monitoring active.`, "success");
            switchServer(serverId);
        } else {
            showToast(res.error || "Connection failed", "error");
        }
    })
    .catch(err => showToast("Connection error: " + err.message, "error"))
    .finally(() => { btn.disabled = false; btn.innerHTML = "Connect"; });
}

// ─── Bootstrap ────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    initAllCharts();
    fetchAll();
    state.polling = setInterval(fetchAll, CONFIG.pollInterval);
    initMetricsWebSocket();

    el("retrainBtn")?.addEventListener("click", triggerRetrain);
    el("testEmailBtn")?.addEventListener("click", triggerTestEmail);

    // Cloud-info modals
    el("modalCloseBtn")?.addEventListener("click", closeModal);
    el("modalCloseBtn2")?.addEventListener("click", closeModal);
    el("cloudModal")?.addEventListener("click", (e) => {
        if (e.target === el("cloudModal")) closeModal();
    });
    
    document.querySelectorAll("[data-cloud]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const p = btn.dataset.cloud;
            if (p === "vps") {
                switchServer(0);
                showToast("Resumed Local PC monitoring.", "success");
            }
            else openModal(p);
        });
    });

    // Add-server modal
    el("btnAddServer")?.addEventListener("click", () =>
        el("addServerModal").classList.add("open"));
    el("addServerCloseBtn")?.addEventListener("click", () =>
        el("addServerModal").classList.remove("open"));

    // Password modal
    el("passwordCloseBtn")?.addEventListener("click", () =>
        el("passwordModal").classList.remove("open"));

    // Tabs & views switcher
    el("tabDashboard")?.addEventListener("click", () => switchView("dashboard"));
    el("tabTerminal")?.addEventListener("click", () => switchView("terminal"));
    el("btnTerminalReset")?.addEventListener("click", () => {
        resetTerminal();
        initTerminal();
    });
    el("btnDisconnectServer")?.addEventListener("click", disconnectActiveServer);

    // Form submissions
    el("addServerForm")?.addEventListener("submit", handleAddServerSubmit);
    el("passwordForm")?.addEventListener("submit", handlePasswordSubmit);

    // Load saved servers into bar
    loadServers();

    // ESC closes any open modal
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            closeModal();
            el("addServerModal")?.classList.remove("open");
            el("passwordModal")?.classList.remove("open");
        }
    });
});
