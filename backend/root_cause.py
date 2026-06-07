"""
root_cause.py — Root Cause Classification Engine
Project : Smart Cloud Pulse AI Monitor (AIOps Edition)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import CPU_HIGH,CPU_DANGER,RAM_DANGER,DISK_HIGH,LOAD_HIGH,NET_SPIKE_MODERATE,NET_SPIKE_HIGH,PROCESS_DROP_HIGH,NET_MIN_SAFE_BASELINE

def classify_root_cause(metric:dict, trend:dict, consecutive_ram_increases:int=0) -> str:
    cpu=float(metric.get('cpu',0)); ram=float(metric.get('ram',0))
    disk=float(metric.get('disk',0)); ni=float(metric.get('network_in',0))
    no=float(metric.get('network_out',0)); la=float(metric.get('load_avg_1m',0))
    cc=int(metric.get('cpu_cores',1))
    rd=trend.get('ram_delta',0); pd=trend.get('process_delta',0)
    ni_avg=float(trend.get('net_in_5min_avg',ni))
    no_avg=float(trend.get('net_out_5min_avg',no))
    
    ni_ratio = ni / max(ni_avg, NET_MIN_SAFE_BASELINE)
    no_ratio = no / max(no_avg, NET_MIN_SAFE_BASELINE)
    lr=la/cc if cc>0 else 0

    if cpu>=CPU_HIGH and ni_ratio>=4.0: return 'Possible DDoS / Traffic Overload'
    if ni_ratio>=8.0: return 'Extreme Inbound Traffic Event'
    if no_ratio>=4.0: return 'Possible Data Exfiltration / Spam'
    if consecutive_ram_increases>=5 and rd>0: return 'Possible Memory Leak'
    if pd<=-PROCESS_DROP_HIGH: return 'Possible Service Crash'
    if disk>=DISK_HIGH: return 'Storage Exhaustion Risk'
    if lr>=LOAD_HIGH and cpu<CPU_HIGH: return 'Possible I/O Bottleneck'
    if cpu>=CPU_DANGER: return 'CPU Resource Exhaustion'
    if ram>=RAM_DANGER: return 'Memory Resource Pressure'
    if ni>=NET_SPIKE_HIGH: return 'Abnormal Inbound Traffic'
    if no>=NET_SPIKE_HIGH: return 'Abnormal Outbound Traffic'
    return 'Abnormal System Behavior'
