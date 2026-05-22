"""Patch backend/app.py: replace connect_server with improved SSH metric collector."""
import re, pathlib

path = pathlib.Path("backend/app.py")
src  = path.read_text()

# Locate the function block by its unique decorator line
start_marker = '@app.route("/api/server/connect", methods=["POST"])'
end_marker   = "# \u2500\u2500\u2500 Health Check"

s = src.index(start_marker)
e = src.index(end_marker)

new_func = '''@app.route("/api/server/connect", methods=["POST"])
def connect_server():
    """
    SSH into a saved VPS, collect full system metrics, run anomaly detection.
    Returns cumulative net_rx_bytes / net_tx_bytes so the dashboard can
    compute per-interval rates across successive poll calls.
    Password is NEVER stored server-side; it is used only for this request.
    """
    try:
        data      = request.get_json(force=True)
        server_id = data.get("server_id")
        password  = data.get("password")
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
            server["ip"], port=server["port"],
            username=server["username"], password=password,
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )

        # Comprehensive bash collector — mirrors local agent metrics exactly
        bash_lines = [
            "set -e",
            # CPU usage over 1 second (interval rate)
            "read -r tag u1 n1 s1 i1 io1 ir1 si1 st1 _ < /proc/stat",
            "prev_total=$((u1+n1+s1+i1+io1+ir1+si1+st1))",
            "prev_idle=$((i1+io1))",
            "sleep 1",
            "read -r tag u2 n2 s2 i2 io2 ir2 si2 st2 _ < /proc/stat",
            "total=$((u2+n2+s2+i2+io2+ir2+si2+st2))",
            "idle=$((i2+io2))",
            "diff_total=$((total-prev_total))",
            "diff_idle=$((idle-prev_idle))",
            "diff_used=$((diff_total-diff_idle))",
            'if [ "$diff_total" -eq 0 ]; then cpu_pct="0.0"; else cpu_pct=$(awk "BEGIN{printf \\"%.1f\\", ($diff_used/$diff_total)*100}"); fi',
            # RAM
            "mem_total=$(awk '/MemTotal/{print $2}' /proc/meminfo)",
            "mem_avail=$(awk '/MemAvailable/{print $2}' /proc/meminfo)",
            'ram_pct=$(awk "BEGIN{printf \\"%.1f\\", (($mem_total-$mem_avail)/$mem_total)*100}")',
            # Disk (root partition)
            "disk_pct=$(df / --output=pcent 2>/dev/null | tail -1 | tr -d ' %')",
            # Process count
            "procs=$(ps -e --no-headers 2>/dev/null | wc -l)",
            # Uptime (handles suspend/resume correctly)
            "uptime_sec=$(cut -d' ' -f1 /proc/uptime)",
            # Network cumulative bytes (all non-loopback interfaces)
            "net_rx=$(awk 'NR>2 && !/lo:/{sum+=$2}  END{printf \"%d\", sum+0}' /proc/net/dev)",
            "net_tx=$(awk 'NR>2 && !/lo:/{sum+=$10} END{printf \"%d\", sum+0}' /proc/net/dev)",
            # Output JSON
            'printf \'{"cpu":%s,"ram":%s,"disk":%s,"processes":%d,"uptime":%s,"net_rx_bytes":%s,"net_tx_bytes":%s}\\n\' '
            '"$cpu_pct" "$ram_pct" "$disk_pct" "$procs" "$uptime_sec" "$net_rx" "$net_tx"',
        ]
        bash_script = "\\n".join(bash_lines)

        _, stdout, stderr = ssh.exec_command(bash_script)
        output = stdout.read().decode("utf-8").strip()
        err    = stderr.read().decode("utf-8").strip()
        ssh.close()

        if not output:
            logger.error("SSH script returned no output. stderr: %s", err)
            return _json_error("No output from remote script", 500)

        try:
            raw = json.loads(output)
        except json.JSONDecodeError:
            logger.error("JSON parse failed. output=%r stderr=%s", output, err)
            return _json_error("Failed to parse remote metrics", 500)

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        metrics = {
            "cpu":         raw["cpu"],
            "ram":         raw["ram"],
            "disk":        raw["disk"],
            "processes":   raw["processes"],
            "uptime":      raw["uptime"],
            # Keep cumulative bytes in network_in/out for anomaly scoring;
            # the dashboard uses net_rx_bytes / net_tx_bytes for rate calc.
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
        return _json_error("Authentication failed \u2014 incorrect password", 401)
    except Exception as exc:
        logger.exception("connect_server error: %s", exc)
        return _json_error("Connection failed: " + str(exc), 500)


'''

patched = src[:s] + new_func + src[e:]
path.write_text(patched)
print("Patched OK. New length:", len(patched))
