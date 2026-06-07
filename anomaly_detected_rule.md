# Anomaly Detection Rules & Alerting Criteria

This document outlines all the static thresholds, machine learning models, noise-suppression gates, and criteria that govern the alerting and email dispatch system in **Smart Cloud Pulse AI Monitor**.

---

## 1. When are Email Alerts Dispatched?

Whenever an anomaly is ingested from a local agent or remote SSH session, the system evaluates the following criteria before executing an email alert:

1. **Email Channel Enabled**: `SMTP_ENABLED=true` must be set in your `.env` configuration.
2. **Severity Threshold**: The severity returned by the hybrid pipeline must be one of:
   * `Moderate Anomaly`
   * `High Anomaly`
   * `Danger`
   *(Severity level `Normal` will never trigger an alert).*
3. **Noise Suppression Gates**: The alert must pass **both** gates in the false positive suppressor:
   * **Gate 1 (Consecutive Count)**: The anomaly must persist for at least `ALERT_CONSECUTIVE_MIN` consecutive polling cycles (default: **2**).
   * **Gate 2 (Cooldown Window)**: The same server must not have triggered an alert within the last `ALERT_COOLDOWN_MINUTES` (default: **5** minutes) to prevent alert storming.

---

## 2. Hard Static Rules & Thresholds

The hybrid pipeline evaluates these static thresholds to determine severity and explain the root causes of the anomaly:

### A. CPU Utilization Rules
* **$\ge$ 95% CPU**: `Danger` severity / `Critical CPU` explanation.
* **$\ge$ 85% CPU**: `High Anomaly` severity / `High CPU` explanation.
* **$\ge$ 70% CPU**: `Moderate Anomaly` severity / `Elevated CPU` explanation.
* **$\ge$ +25% Spike (1 poll)**: Flagged as a `Rapid CPU spike`.
* **$\ge$ +15% Spike (1 poll)**: Flagged as `CPU climbing`.

### B. Memory (RAM) Utilization Rules
* **$\ge$ 95% RAM**: `Danger` severity / `Critical RAM` explanation.
* **$\ge$ 90% RAM**: `High Anomaly` severity / `High RAM` explanation.
* **$\ge$ 80% RAM**: `Moderate Anomaly` severity / `Elevated RAM` explanation.
* **$\ge$ +15% Growth (1 poll)**: Flagged as `Memory increasing rapidly`.
* **$\ge$ +8% Growth (1 poll)**: Flagged as `Memory rising`.

### C. System Load Ratio Rules (`Load Average (1m) / CPU Cores`)
* **$\ge$ 1.0 Load Ratio**: `Danger` severity / `System overloaded` explanation.
* **$\ge$ 0.85 Load Ratio**: `High Anomaly` severity / `High load ratio` explanation.
* **$\ge$ 0.70 Load Ratio**: `Moderate Anomaly` severity / `Elevated load ratio` explanation.

### D. Disk Utilization Rules
* **$\ge$ 95% Disk**: Flagged as `Disk critically full`.
* **$\ge$ 85% Disk**: Flagged as `High disk`.
* **$\ge$ 70% Disk**: Flagged as `Disk filling`.

### E. Network Rate Spike Rules (Inbound / Outbound)
The system calculates the network deviation ratio relative to the 5-minute rolling baseline floor $\text{ratio} = \frac{\text{current\_rate}}{\max(\text{baseline}, \text{safe\_floor})}$ (where `NET_MIN_SAFE_BASELINE = 100 KB/s` is the safe floor).
* **$\ge$ 8.0x Ratio**: `Danger` severity (`Extreme inbound/outbound traffic`).
* **$\ge$ 4.0x Ratio**: `High Anomaly` severity (`Very high inbound/outbound traffic`).
* **$\ge$ 2.0x Ratio**: `Moderate Anomaly` severity (`Elevated inbound/outbound traffic`).
* **Absolute Fallbacks**:
  * **$\ge$ 50 MB/s**: `Danger` severity.
  * **$\ge$ 20 MB/s**: `High Anomaly` severity.
  * **$\ge$ 5 MB/s**: `Moderate Anomaly` severity.

### F. Running Process Drop Rules (Possible Crashes)
* **$\ge$ 30 Process Drop (1 poll)**: Flagged as `Severe process drop`.
* **$\ge$ 15 Process Drop (1 poll)**: Flagged as `Process count dropped`.
* **$\ge$ 5 Process Drop (1 poll)**: Flagged as `Process count fell`.

---

## 3. AI ML Detection Fallback (Isolation Forest)

If none of the hard static thresholds above are breached, the **Isolation Forest** model evaluates the multi-dimensional feature vector. 

* **Feature Vector**: CPU Distance, RAM Distance, Disk, Network In, Network Out, Processes, Uptime, CPU Delta, RAM Delta, Disk Delta, Process Delta, Net In Delta, Net Out Delta, CPU 5m Avg, RAM 5m Avg, Net In 5m Avg, and Process 5m Avg.
* **AI Severity-Aware Scoring**:
  * **Score $\le$ -0.85**: `Danger` severity.
  * **Score $\le$ -0.70**: `High Anomaly` severity.
  * **Score $\le$ -0.55**: `Moderate Anomaly` severity.
  * *Scores > -0.55 are treated as `Normal`.*

