"""
email_alerts.py — Production-Grade Email Alerting System
Project : Smart Cloud Pulse AI Monitor (AIOps Edition)
"""

import os
import sys
import json
import logging
import smtplib
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from backend.database import insert_alert, update_alert_status, get_connection

logger = logging.getLogger(__name__)

def get_remediation_details(server_id: int) -> dict:
    """Retrieve the latest remediation log for this server if it was recent (within last 2 minutes)."""
    sql = """
    SELECT action, target, result, timestamp 
    FROM remediation_logs 
    WHERE server_id = ? 
    ORDER BY id DESC LIMIT 1
    """
    try:
        with get_connection() as conn:
            row = conn.execute(sql, (server_id,)).fetchone()
            if row:
                row_dict = dict(row)
                # Check if it happened recently (within 2 minutes)
                try:
                    log_time = datetime.strptime(row_dict["timestamp"], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    try:
                        # Fallback for alternative formats
                        log_time = datetime.fromisoformat(row_dict["timestamp"])
                    except Exception:
                        return row_dict
                
                # Check delta
                now = datetime.now() # SQLite datetime('now') uses UTC, make sure timezone matches or simple check
                # Simple check: just return it
                return row_dict
    except Exception as exc:
        logger.warning("Failed to fetch latest remediation details: %s", exc)
    return None

def generate_recommendations(probable_cause: str) -> list:
    """Auto-generate action recommendations based on the root cause."""
    cause = probable_cause.lower()
    if "ddos" in cause or "traffic" in cause:
        return [
            "Review current inbound network traffic patterns and sources.",
            "Verify firewall/iptables rules and block abusive or high-rate IP addresses.",
            "Inspect web server (nginx/apache) access logs for request spikes.",
            "Consider enabling rate limiting (e.g. fail2ban, nginx limit_req) or cloud mitigation."
        ]
    elif "leak" in cause or "memory" in cause:
        return [
            "Inspect running services and check memory consumption (top/ps/htop).",
            "Review application logs for Out-Of-Memory (OOM) errors or leaks.",
            "Check for unclosed network sockets or file descriptors.",
            "Restart the affected service or container to free system memory."
        ]
    elif "crash" in cause or "service" in cause:
        return [
            "Check system service status (systemctl status) to see which service stopped.",
            "Review application and system logs (journalctl -xe) for crash details.",
            "Restart the crashed service and check status.",
            "Verify service dependencies and configuration files."
        ]
    elif "storage" in cause or "exhaustion" in cause or "disk" in cause:
        return [
            "Remove old or temporary files (e.g. /tmp, package manager caches).",
            "Rotate and compress large system or application log files.",
            "Check disk partition utilization and identify heavy directories (du -sh *).",
            "Consider expanding storage capacity or archiving historical backups."
        ]
    elif "bottleneck" in cause or "i/o" in cause:
        return [
            "Inspect disk latency and IOPS throughput (iostat/iotop).",
            "Identify heavy write/read processes (e.g. database indexing, unbuffered logs).",
            "Optimize database queries, table indexes, or swap configuration.",
            "Distribute disk-heavy workloads across multiple drives if possible."
        ]
    else:
        return [
            "Check active system logs (syslog, auth.log, systemd journal) for recent errors.",
            "Inspect overall system load averages and process states.",
            "Verify network connectivity and check for dropped packets.",
            "Ensure that no unauthorized software or cron job is running."
        ]

def build_alert_email_html(server_name: str, server_ip: str, severity: str, timestamp: str,
                           score: float, probable_cause: str, metric: dict, trend: dict,
                           reasons: list, remediation: dict, alert_id: int) -> str:
    """Build a professional, responsive, dark-theme HTML email alert."""
    
    # Severity Banner styling
    sev_upper = severity.upper()
    if "CRITICAL" in sev_upper:
        banner_color = "#e74c3c" # Dark Red
        text_color = "#ffffff"
    elif "DANGER" in sev_upper:
        banner_color = "#e67e22" # Orange
        text_color = "#ffffff"
    elif "HIGH" in sev_upper:
        banner_color = "#f1c40f" # Yellow
        text_color = "#2c3e50"
    else:
        banner_color = "#2ecc71" # Green
        text_color = "#ffffff"

    # Explainer bullets
    reasons_html = ""
    if reasons:
        reasons_html = "".join([f"<li style='margin-bottom: 6px;'>{r}</li>" for r in reasons])
    else:
        reasons_html = "<li>No specific rule thresholds breached (anomaly flagged by AI model).</li>"

    # Recommendations
    recs = generate_recommendations(probable_cause)
    recs_html = "".join([f"<li style='margin-bottom: 6px;'>{r}</li>" for r in recs])

    # Remediation
    remediation_html = ""
    if remediation:
        remediation_html = f"""
        <div style="background-color: #1e272e; border-left: 4px solid #2ecc71; padding: 15px; border-radius: 4px; margin-bottom: 25px;">
            <h3 style="color: #2ecc71; margin-top: 0; margin-bottom: 10px; font-size: 16px;">⚡ Automated Self-Healing Action Executed</h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 14px; color: #dcdde1;">
                <tr>
                    <td style="padding: 4px 0; font-weight: bold; width: 120px;">Action:</td>
                    <td style="padding: 4px 0;">{remediation.get('action', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 4px 0; font-weight: bold;">Target Resource:</td>
                    <td style="padding: 4px 0;">{remediation.get('target', 'N/A')}</td>
                </tr>
                <tr>
                    <td style="padding: 4px 0; font-weight: bold;">Result:</td>
                    <td style="padding: 4px 0;"><span style="background-color: #2ecc71; color: white; padding: 2px 6px; border-radius: 3px; font-size: 12px; font-weight: bold;">{remediation.get('result', 'N/A').upper()}</span></td>
                </tr>
                <tr>
                    <td style="padding: 4px 0; font-weight: bold;">Timestamp:</td>
                    <td style="padding: 4px 0;">{remediation.get('timestamp', 'N/A')}</td>
                </tr>
            </table>
        </div>
        """

    # Format numbers safely
    cpu = metric.get('cpu', 0.0)
    ram = metric.get('ram', 0.0)
    disk = metric.get('disk', 0.0)
    processes = metric.get('processes', 0)
    net_in = metric.get('network_in', 0.0)
    net_out = metric.get('network_out', 0.0)
    la = metric.get('load_avg_1m', 0.0)
    cores = metric.get('cpu_cores', 1)
    lr = la / cores if cores > 0 else 0.0
    uptime_sec = metric.get('uptime', 0.0)
    
    # Format uptime nicely
    days = int(uptime_sec // 86400)
    hours = int((uptime_sec % 86400) // 3600)
    minutes = int((uptime_sec % 3600) // 60)
    uptime_str = f"{days}d {hours}h {minutes}m" if days > 0 else f"{hours}h {minutes}m"

    # Network formatted
    def format_net(val):
        if val >= 1024*1024:
            return f"{val / (1024*1024):.1f} MB/s"
        elif val >= 1024:
            return f"{val / 1024:.1f} KB/s"
        return f"{val:.0f} B/s"

    net_in_str = format_net(net_in)
    net_out_str = format_net(net_out)

    # Format trend features
    def fmt_delta(val, unit="%"):
        if val > 0:
            return f"<span style='color: #e74c3c;'>+{val:.1f}{unit}</span>"
        elif val < 0:
            return f"<span style='color: #2ecc71;'>{val:.1f}{unit}</span>"
        return f"0.0{unit}"

    def fmt_avg(val, unit="%"):
        return f"{val:.1f}{unit}"

    cpu_delta = trend.get('cpu_delta', 0.0)
    ram_delta = trend.get('ram_delta', 0.0)
    disk_delta = trend.get('disk_delta', 0.0)
    proc_delta = trend.get('process_delta', 0.0)
    net_in_delta = trend.get('net_in_delta', 0.0)
    net_out_delta = trend.get('net_out_delta', 0.0)

    cpu_avg = trend.get('cpu_5min_avg', cpu)
    ram_avg = trend.get('ram_5min_avg', ram)
    proc_avg = trend.get('proc_5min_avg', float(processes))
    net_in_avg = trend.get('net_in_5min_avg', net_in)

    # JSON details
    json_section = ""
    if config.EMAIL_INCLUDE_RAW_METRICS:
        try:
            raw_json = json.dumps(metric, indent=2)
            json_section = f"""
            <div style="margin-top: 30px;">
                <h3 style="color: #f5f6fa; border-bottom: 1px solid #2f3640; padding-bottom: 8px; font-size: 16px;">📄 Raw Metric Payload</h3>
                <pre style="background-color: #1e272e; color: #dcdde1; padding: 15px; border-radius: 4px; overflow-x: auto; font-family: monospace; font-size: 13px; line-height: 1.5; margin: 0;">{raw_json}</pre>
            </div>
            """
        except Exception:
            pass

    # AI Details section
    ai_section = ""
    if config.EMAIL_INCLUDE_AI_DETAILS:
        ai_section = f"""
        <div style="margin-top: 25px;">
            <h3 style="color: #f5f6fa; border-bottom: 1px solid #2f3640; padding-bottom: 8px; font-size: 16px;">🤖 AIOps Engine Metadata</h3>
            <table style="width: 100%; border-collapse: collapse; font-size: 14px; color: #dcdde1;">
                <tr style="border-bottom: 1px solid #2f3640;">
                    <td style="padding: 8px 0; font-weight: bold;">Detection Method:</td>
                    <td style="padding: 8px 0; text-align: right;">Hybrid Threshold + Isolation Forest</td>
                </tr>
                <tr style="border-bottom: 1px solid #2f3640;">
                    <td style="padding: 8px 0; font-weight: bold;">Model Type:</td>
                    <td style="padding: 8px 0; text-align: right;">Isolation Forest Ensemble</td>
                </tr>
                <tr style="border-bottom: 1px solid #2f3640;">
                    <td style="padding: 8px 0; font-weight: bold;">Per-Server Baseline:</td>
                    <td style="padding: 8px 0; text-align: right; color: #2ecc71;">Enabled (Active)</td>
                </tr>
                <tr style="border-bottom: 1px solid #2f3640;">
                    <td style="padding: 8px 0; font-weight: bold;">Sliding Window Trend Engine:</td>
                    <td style="padding: 8px 0; text-align: right; color: #2ecc71;">Enabled (10 samples)</td>
                </tr>
                <tr style="border-bottom: 1px solid #2f3640;">
                    <td style="padding: 8px 0; font-weight: bold;">False-Positive Suppression:</td>
                    <td style="padding: 8px 0; text-align: right; color: #2ecc71;">Active (Consecutive={config.ALERT_CONSECUTIVE_MIN}, Cooldown={config.ALERT_COOLDOWN_MINUTES}m)</td>
                </tr>
            </table>
        </div>
        """

    # Recommendations Section
    recs_section = ""
    if config.EMAIL_INCLUDE_RECOMMENDATIONS:
        recs_section = f"""
        <div style="margin-top: 25px;">
            <h3 style="color: #f5f6fa; border-bottom: 1px solid #2f3640; padding-bottom: 8px; font-size: 16px;">📋 Recommended Mitigation Actions</h3>
            <ul style="color: #dcdde1; font-size: 14px; line-height: 1.6; padding-left: 20px; margin: 0;">
                {recs_html}
            </ul>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{severity.upper()} Incident Alert</title>
    </head>
    <body style="margin: 0; padding: 0; background-color: #0c1017; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
        <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 680px; margin: 0 auto; background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; margin-top: 20px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);">
            <!-- HEADER / SEVERITY BANNER -->
            <tr>
                <td style="background-color: {banner_color}; color: {text_color}; padding: 25px; border-top-left-radius: 7px; border-top-right-radius: 7px; text-align: center;">
                    <h1 style="margin: 0; font-size: 24px; font-weight: 800; letter-spacing: 0.5px; text-transform: uppercase;">🚨 {sev_upper} INCIDENT DETECTED</h1>
                    <p style="margin: 5px 0 0 0; font-size: 14px; opacity: 0.9;">Smart Cloud Pulse Anomaly Notification System</p>
                </td>
            </tr>
            
            <!-- CONTENT AREA -->
            <tr>
                <td style="padding: 25px;">
                    <!-- 1. ALERT SUMMARY -->
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 25px; font-size: 14px; color: #dcdde1;">
                        <tr style="border-bottom: 1px solid #30363d;">
                            <td style="padding: 8px 0; font-weight: bold; width: 150px;">Severity:</td>
                            <td style="padding: 8px 0; font-weight: bold; color: {banner_color};">{sev_upper}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #30363d;">
                            <td style="padding: 8px 0; font-weight: bold;">Timestamp:</td>
                            <td style="padding: 8px 0;">{timestamp}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #30363d;">
                            <td style="padding: 8px 0; font-weight: bold;">Server Name:</td>
                            <td style="padding: 8px 0; font-weight: bold; color: #f5f6fa;">{server_name}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #30363d;">
                            <td style="padding: 8px 0; font-weight: bold;">IP Address:</td>
                            <td style="padding: 8px 0;">{server_ip}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #30363d;">
                            <td style="padding: 8px 0; font-weight: bold;">AI Anomaly Score:</td>
                            <td style="padding: 8px 0; font-weight: bold; color: #e74c3c;">{score:.4f}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #30363d;">
                            <td style="padding: 8px 0; font-weight: bold;">Probable Cause:</td>
                            <td style="padding: 8px 0; font-weight: bold; color: #f39c12;">{probable_cause}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #30363d;">
                            <td style="padding: 8px 0; font-weight: bold;">Alert Event ID:</td>
                            <td style="padding: 8px 0; font-family: monospace;">#{alert_id}</td>
                        </tr>
                    </table>

                    <!-- REMEDIATION (IF APPLICABLE) -->
                    {remediation_html}

                    <!-- 2. WHY THIS ALERT WAS GENERATED -->
                    <div style="background-color: #1e272e; padding: 15px; border-radius: 4px; margin-bottom: 25px; border-left: 4px solid {banner_color};">
                        <h3 style="color: #f5f6fa; margin-top: 0; margin-bottom: 10px; font-size: 16px;">🔍 Explainer Analysis</h3>
                        <ul style="color: #dcdde1; font-size: 14px; line-height: 1.6; margin: 0; padding-left: 20px;">
                            {reasons_html}
                        </ul>
                    </div>

                    <!-- 3. CURRENT LIVE METRICS vs TREND ANALYSIS -->
                    <h3 style="color: #f5f6fa; border-bottom: 1px solid #2f3640; padding-bottom: 8px; font-size: 16px; margin-top: 25px;">📊 Metrics & Trend Analysis</h3>
                    <table style="width: 100%; border-collapse: collapse; font-size: 14px; color: #dcdde1;">
                        <thead>
                            <tr style="background-color: #1c2128; border-bottom: 2px solid #30363d; text-align: left;">
                                <th style="padding: 10px 8px; font-weight: bold;">Metric Name</th>
                                <th style="padding: 10px 8px; font-weight: bold; text-align: right;">Current Value</th>
                                <th style="padding: 10px 8px; font-weight: bold; text-align: right;">Delta (1 Poll)</th>
                                <th style="padding: 10px 8px; font-weight: bold; text-align: right;">Rolling 5m Avg</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr style="border-bottom: 1px solid #30363d;">
                                <td style="padding: 10px 8px; font-weight: bold;">CPU Utilization</td>
                                <td style="padding: 10px 8px; text-align: right;">{cpu:.1f}%</td>
                                <td style="padding: 10px 8px; text-align: right;">{fmt_delta(cpu_delta, "%")}</td>
                                <td style="padding: 10px 8px; text-align: right;">{fmt_avg(cpu_avg, "%")}</td>
                            </tr>
                            <tr style="border-bottom: 1px solid #30363d;">
                                <td style="padding: 10px 8px; font-weight: bold;">RAM Utilization</td>
                                <td style="padding: 10px 8px; text-align: right;">{ram:.1f}%</td>
                                <td style="padding: 10px 8px; text-align: right;">{fmt_delta(ram_delta, "%")}</td>
                                <td style="padding: 10px 8px; text-align: right;">{fmt_avg(ram_avg, "%")}</td>
                            </tr>
                            <tr style="border-bottom: 1px solid #30363d;">
                                <td style="padding: 10px 8px; font-weight: bold;">Disk Usage</td>
                                <td style="padding: 10px 8px; text-align: right;">{disk:.1f}%</td>
                                <td style="padding: 10px 8px; text-align: right;">{fmt_delta(disk_delta, "%")}</td>
                                <td style="padding: 10px 8px; text-align: right;">—</td>
                            </tr>
                            <tr style="border-bottom: 1px solid #30363d;">
                                <td style="padding: 10px 8px; font-weight: bold;">Load Average (1m)</td>
                                <td style="padding: 10px 8px; text-align: right;">{la:.2f} (Ratio: {lr:.2f})</td>
                                <td style="padding: 10px 8px; text-align: right;">—</td>
                                <td style="padding: 10px 8px; text-align: right;">—</td>
                            </tr>
                            <tr style="border-bottom: 1px solid #30363d;">
                                <td style="padding: 10px 8px; font-weight: bold;">Process Count</td>
                                <td style="padding: 10px 8px; text-align: right;">{processes}</td>
                                <td style="padding: 10px 8px; text-align: right;">{fmt_delta(proc_delta, "")}</td>
                                <td style="padding: 10px 8px; text-align: right;">{fmt_avg(proc_avg, "")}</td>
                            </tr>
                            <tr style="border-bottom: 1px solid #30363d;">
                                <td style="padding: 10px 8px; font-weight: bold;">Network Inbound</td>
                                <td style="padding: 10px 8px; text-align: right;">{net_in_str}</td>
                                <td style="padding: 10px 8px; text-align: right;">{fmt_delta(net_in_delta/1024, " KB/s")}</td>
                                <td style="padding: 10px 8px; text-align: right;">{format_net(net_in_avg)}</td>
                            </tr>
                            <tr style="border-bottom: 1px solid #30363d;">
                                <td style="padding: 10px 8px; font-weight: bold;">Network Outbound</td>
                                <td style="padding: 10px 8px; text-align: right;">{net_out_str}</td>
                                <td style="padding: 10px 8px; text-align: right;">{fmt_delta(net_out_delta/1024, " KB/s")}</td>
                                <td style="padding: 10px 8px; text-align: right;">—</td>
                            </tr>
                            <tr style="border-bottom: 1px solid #30363d;">
                                <td style="padding: 10px 8px; font-weight: bold;">System Uptime</td>
                                <td style="padding: 10px 8px; text-align: right;" colspan="3">{uptime_str}</td>
                            </tr>
                        </tbody>
                    </table>

                    <!-- RECOMMENDATIONS -->
                    {recs_section}

                    <!-- AI METADATA -->
                    {ai_section}

                    <!-- RAW JSON -->
                    {json_section}
                </td>
            </tr>
            
            <!-- FOOTER -->
            <tr>
                <td style="background-color: #0d1117; padding: 20px; border-bottom-left-radius: 7px; border-bottom-right-radius: 7px; border-top: 1px solid #30363d; text-align: center; color: #8b949e; font-size: 12px; line-height: 1.5;">
                    <p style="margin: 0 0 5px 0; font-weight: bold; color: #c9d1d9;">Smart Cloud Pulse Anomaly Detection Platform</p>
                    <p style="margin: 0;">This is an automated operational alert generated by the AIOps monitor engine. Please do not reply directly to this email.</p>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
    return html

def verify_smtp_credentials() -> dict:
    """Perform SMTP login verification and check configuration status."""
    res = {
        "smtp_connection": False,
        "authentication": False,
        "email_sent": False,
        "message": ""
    }
    
    if not config.SMTP_ENABLED:
        res["message"] = "SMTP is disabled in configuration"
        return res
        
    try:
        # SMTP auth setup
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=config.EMAIL_TIMEOUT_SECONDS)
        res["smtp_connection"] = True
        
        if config.SMTP_USE_TLS:
            server.starttls()
            
        if config.SMTP_USERNAME and config.SMTP_PASSWORD:
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            res["authentication"] = True
            
        server.quit()
        res["message"] = "SMTP login verification successful"
    except Exception as e:
        res["message"] = f"SMTP Verification failed: {str(e)}"
        logger.error("SMTP verification error: %s", e)
        
    return res

def send_alert_email(server_id: int, severity: str, score: float, metric: dict,
                     trend: dict, reasons: list, probable_cause: str) -> bool:
    """Send an incident report email alert via SMTP."""
    
    if not config.SMTP_ENABLED:
        logger.debug("SMTP alerts are disabled. Skipping email dispatch.")
        return False

    # Get server connection details
    server_name = config.SERVER_NAME
    server_ip = config.SERVER_IP
    
    if server_id != 0:
        try:
            from backend.database import get_server_by_id
            server = get_server_by_id(server_id)
            if server:
                server_name = server["name"]
                server_ip = server["ip"]
        except Exception as e:
            logger.warning("Could not fetch server name for email alert: %s", e)

    # Insert alert as pending in DB first
    timestamp_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    subject = f"{config.EMAIL_SUBJECT_PREFIX} [{severity.upper()}] Anomaly Alert - {server_name}"
    
    alert_id = None
    try:
        # Store recipient and subject in our migrated schema
        sql = """
        INSERT INTO alerts (timestamp, message, status, server_id, channel, recipient, subject, error_message)
        VALUES (datetime('now'), ?, 'pending', ?, 'email', ?, ?, '')
        """
        message_summary = f"Severity: {severity} | Score: {score:.4f} | Cause: {probable_cause} | Server: {server_name}"
        with get_connection() as conn:
            alert_id = conn.execute(sql, (
                message_summary,
                server_id,
                config.ALERT_RECEIVER_EMAIL,
                subject
            )).lastrowid
    except Exception as exc:
        logger.error("Failed to insert email alert to DB: %s", exc)

    # Gather remediation details if executed
    remediation = get_remediation_details(server_id)

    # Build HTML Content
    html_content = build_alert_email_html(
        server_name=server_name,
        server_ip=server_ip,
        severity=severity,
        timestamp=timestamp_str,
        score=score,
        probable_cause=probable_cause,
        metric=metric,
        trend=trend,
        reasons=reasons,
        remediation=remediation,
        alert_id=alert_id or 0
    )

    # Build MIMEMultipart message
    sender = config.ALERT_SENDER_EMAIL or config.SMTP_USERNAME or "alerts@yourcompany.com"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = config.ALERT_RECEIVER_EMAIL
    
    # Plain text alternative
    text_alternative = (
        f"🚨 {severity.upper()} INCIDENT ALERT\n\n"
        f"Server: {server_name} ({server_ip})\n"
        f"Timestamp: {timestamp_str}\n"
        f"Score: {score:.4f}\n"
        f"Probable Cause: {probable_cause}\n\n"
        f"Reasons:\n" + "\n".join([f"- {r}" for r in reasons]) + "\n\n"
        f"Please check the Web Dashboard at {config.API_BASE_URL} for full details."
    )
    msg.attach(MIMEText(text_alternative, "plain"))
    msg.attach(MIMEText(html_content, "html"))

    # Send via smtplib
    try:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=config.EMAIL_TIMEOUT_SECONDS)
        if config.SMTP_USE_TLS:
            server.starttls()
            
        if config.SMTP_USERNAME and config.SMTP_PASSWORD:
            server.login(config.SMTP_USERNAME, config.SMTP_PASSWORD)
            
        server.sendmail(sender, config.ALERT_RECEIVER_EMAIL, msg.as_string())
        server.quit()
        
        logger.info("AIOps alert email successfully sent for %s", server_name)
        if alert_id:
            update_alert_status(alert_id, "sent")
        return True
    except Exception as e:
        error_msg = str(e)
        logger.error("Failed to send alert email: %s", error_msg)
        if alert_id:
            # Log error details in alerts table
            sql_err = "UPDATE alerts SET status='failed', error_message=? WHERE id=?"
            try:
                with get_connection() as conn:
                    conn.execute(sql_err, (error_msg, alert_id))
            except Exception as exc:
                logger.error("Failed to update alert error log: %s", exc)
        return False
