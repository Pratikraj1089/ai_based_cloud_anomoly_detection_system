"""
explainer.py — Anomaly Explanation Engine
Project : Smart Cloud Pulse AI Monitor (AIOps Edition)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (CPU_MODERATE,CPU_HIGH,CPU_DANGER,RAM_MODERATE,RAM_HIGH,RAM_DANGER,
                    DISK_MODERATE,DISK_HIGH,DISK_DANGER,LOAD_MODERATE,LOAD_HIGH,LOAD_DANGER,
                    NET_SPIKE_MODERATE,NET_SPIKE_HIGH,NET_SPIKE_DANGER,
                    PROCESS_DROP_MODERATE,PROCESS_DROP_HIGH,PROCESS_DROP_DANGER,
                    NET_MIN_SAFE_BASELINE,
                    IFOREST_MODERATE_THRESHOLD, IFOREST_HIGH_THRESHOLD, IFOREST_DANGER_THRESHOLD)

def explain(metric:dict, trend:dict, prediction:dict) -> list:
    r=[]
    cpu=float(metric.get('cpu',0)); ram=float(metric.get('ram',0))
    disk=float(metric.get('disk',0)); ni=float(metric.get('network_in',0))
    no=float(metric.get('network_out',0)); pr=float(metric.get('processes',0))
    la=float(metric.get('load_avg_1m',0)); cc=int(metric.get('cpu_cores',1))
    cd=trend.get('cpu_delta',0); rd=trend.get('ram_delta',0)
    pd=trend.get('process_delta',0); nid=trend.get('net_in_delta',0)
    nod=trend.get('net_out_delta',0); pa=trend.get('proc_5min_avg',pr)
    score=prediction.get('score',0); lr=la/cc if cc>0 else 0
    if cpu>=CPU_DANGER: r.append(f'Critical CPU: {cpu:.1f}%')
    elif cpu>=CPU_HIGH: r.append(f'High CPU: {cpu:.1f}%')
    elif cpu>=CPU_MODERATE: r.append(f'Elevated CPU: {cpu:.1f}%')
    if cd>=25: r.append(f'Rapid CPU spike: +{cd:.1f}% since last reading')
    elif cd>=15: r.append(f'CPU climbing: +{cd:.1f}%')
    if ram>=RAM_DANGER: r.append(f'Critical RAM: {ram:.1f}%')
    elif ram>=RAM_HIGH: r.append(f'High RAM: {ram:.1f}%')
    elif ram>=RAM_MODERATE: r.append(f'Elevated RAM: {ram:.1f}%')
    if rd>=15: r.append(f'Memory increasing rapidly: +{rd:.1f}%')
    elif rd>=8: r.append(f'Memory rising: +{rd:.1f}%')
    if disk>=DISK_DANGER: r.append(f'Disk critically full: {disk:.1f}%')
    elif disk>=DISK_HIGH: r.append(f'High disk: {disk:.1f}%')
    elif disk>=DISK_MODERATE: r.append(f'Disk filling: {disk:.1f}%')
    if pd<=-PROCESS_DROP_DANGER: r.append(f'Severe process drop: {abs(pd):.0f} gone — possible crash')
    elif pd<=-PROCESS_DROP_HIGH: r.append(f'Process count dropped: {abs(pd):.0f}')
    elif pd<=-PROCESS_DROP_MODERATE: r.append(f'Process count fell: {abs(pd):.0f}')
    if lr>=LOAD_DANGER: r.append(f'System overloaded: load ratio {lr:.2f} (load={la:.1f},cores={cc})')
    elif lr>=LOAD_HIGH: r.append(f'High load ratio: {lr:.2f}')
    elif lr>=LOAD_MODERATE: r.append(f'Elevated load ratio: {lr:.2f}')

    # Format net rates helper
    def format_net(val):
        if val >= 1024*1024:
            return f"{val / (1024*1024):.1f} MB/s"
        elif val >= 1024:
            return f"{val / 1024:.1f} KB/s"
        return f"{val:.0f} B/s"

    ni_avg = float(trend.get('net_in_5min_avg', ni) if trend else ni)
    no_avg = float(trend.get('net_out_5min_avg', no) if trend else no)
    ni_ratio = ni / max(ni_avg, NET_MIN_SAFE_BASELINE)
    no_ratio = no / max(no_avg, NET_MIN_SAFE_BASELINE)

    # Inbound network rules
    if ni_ratio >= 8.0:
        r.append(f'Extreme inbound traffic: {format_net(ni)} ({ni_ratio:.1f}x higher than normal baseline of {format_net(ni_avg)}) — possible DDoS')
    elif ni_ratio >= 4.0:
        r.append(f'Very high inbound traffic: {format_net(ni)} ({ni_ratio:.1f}x higher than normal baseline of {format_net(ni_avg)})')
    elif ni_ratio >= 2.0:
        r.append(f'Elevated inbound traffic: {format_net(ni)} ({ni_ratio:.1f}x higher than normal baseline of {format_net(ni_avg)})')
    elif ni >= NET_SPIKE_DANGER:
        r.append(f'Extreme inbound (absolute fallback): {format_net(ni)} — possible DDoS')
    elif ni >= NET_SPIKE_HIGH:
        r.append(f'Very high inbound (absolute fallback): {format_net(ni)}')
    elif ni >= NET_SPIKE_MODERATE:
        r.append(f'Elevated inbound (absolute fallback): {format_net(ni)}')

    # Outbound network rules
    if no_ratio >= 8.0:
        r.append(f'Extreme outbound traffic: {format_net(no)} ({no_ratio:.1f}x higher than normal baseline of {format_net(no_avg)}) — possible exfiltration')
    elif no_ratio >= 4.0:
        r.append(f'Very high outbound traffic: {format_net(no)} ({no_ratio:.1f}x higher than normal baseline of {format_net(no_avg)})')
    elif no_ratio >= 2.0:
        r.append(f'Elevated outbound traffic: {format_net(no)} ({no_ratio:.1f}x higher than normal baseline of {format_net(no_avg)})')
    elif no >= NET_SPIKE_DANGER:
        r.append(f'Extreme outbound (absolute fallback): {format_net(no)} — possible exfiltration')
    elif no >= NET_SPIKE_HIGH:
        r.append(f'Very high outbound (absolute fallback): {format_net(no)}')
    elif no >= NET_SPIKE_MODERATE:
        r.append(f'Elevated outbound (absolute fallback): {format_net(no)}')

    # AI Score Severity explanations
    if score <= IFOREST_DANGER_THRESHOLD:
        r.append(f'AI Severity: Danger (Isolation Forest Score: {score:.4f}) — Traffic pattern significantly deviates from learned behavior')
    elif score <= IFOREST_HIGH_THRESHOLD:
        r.append(f'AI Severity: High (Isolation Forest Score: {score:.4f}) — Traffic pattern significantly deviates from learned behavior')
    elif score <= IFOREST_MODERATE_THRESHOLD:
        r.append(f'AI Severity: Moderate (Isolation Forest Score: {score:.4f}) — Traffic pattern significantly deviates from learned behavior')

    return r
