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
    pollInterval: 10_000, // ms
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
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
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
        return `
      <tr>
        <td>${formatTime(a.timestamp)}</td>
        <td>${vals ? vals.cpu?.toFixed(1) + "%" : "—"}</td>
        <td>${vals ? vals.ram?.toFixed(1) + "%" : "—"}</td>
        <td>${a.anomaly_score?.toFixed(4)}</td>
        <td><span class="sev-badge ${a.severity}">${a.severity}</span></td>
      </tr>`;
    }).join("");
}

// ─── Model Info Panel ─────────────────────────────────────────────────────────
function renderModelPanel(status) {
    if (!status) return;
    const model = status.model || {};
    const total = status.total_metrics || 0;
    const trainSamples = 200;
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
        const [metricRes, anomalyRes, statusRes] = await Promise.all([
            apiFetch("/api/metrics/latest?limit=100"),
            apiFetch("/api/anomalies?limit=50"),
            apiFetch("/api/status"),
        ]);

        const metrics = metricRes.data || [];
        const anomalies = anomalyRes.data || [];
        const statusD = statusRes.data || {};

        // First load: rebuild charts; subsequent: incremental
        if (state.metrics.length === 0 && metrics.length > 0) {
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
        updateStatCards(latest);
        updateStatusBadge(statusD);
        renderAnomalyTable(anomalies);
        renderModelPanel(statusD);

        el("apiErrorBanner") && (el("apiErrorBanner").style.display = "none");

    } catch (err) {
        console.warn("Poll failed:", err);
        const banner = el("apiErrorBanner");
        if (banner) banner.style.display = "block";
    }
}

// ─── Manual Retrain ───────────────────────────────────────────────────────────
async function triggerRetrain() {
    const btn = el("retrainBtn");
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner"></span> Training…`;
    try {
        const res = await apiFetch("/api/train", {
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

// ─── Bootstrap ────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
    initAllCharts();

    // Initial fetch
    fetchAll();

    // Polling
    state.polling = setInterval(fetchAll, CONFIG.pollInterval);

    // Retrain button
    el("retrainBtn")?.addEventListener("click", triggerRetrain);

    // Modal close handlers
    el("modalCloseBtn")?.addEventListener("click", closeModal);
    el("modalCloseBtn2")?.addEventListener("click", closeModal);
    el("cloudModal")?.addEventListener("click", (e) => {
        if (e.target === el("cloudModal")) closeModal();
    });

    // Cloud provider buttons
    document.querySelectorAll("[data-cloud]").forEach((btn) => {
        btn.addEventListener("click", () => {
            const provider = btn.dataset.cloud;
            if (provider === "vps") {
                showToast("✅ VPS Agent is active and collecting data.", "success");
            } else {
                openModal(provider);
            }
        });
    });

    // Keyboard: Escape closes modal
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeModal();
    });
});
