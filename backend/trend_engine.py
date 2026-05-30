"""
trend_engine.py — Sliding Window Trend Feature Calculator
Project : Smart Cloud Pulse AI Monitor (AIOps Edition)
"""
import time, logging
from collections import deque
from typing import Dict
logger = logging.getLogger(__name__)
_WINDOW_SIZE = 10

class _TrendEngine:
    def __init__(self, window_size=_WINDOW_SIZE):
        self._w = window_size
        self._bufs: Dict[int,deque] = {}
    def _buf(self, sid):
        sid = int(sid or 0)
        if sid not in self._bufs: self._bufs[sid] = deque(maxlen=self._w)
        return self._bufs[sid]
    def push(self, server_id, metric):
        self._buf(server_id).append({
            'ts':time.time(),'cpu':float(metric.get('cpu',0)),
            'ram':float(metric.get('ram',0)),'disk':float(metric.get('disk',0)),
            'processes':float(metric.get('processes',0)),
            'network_in':float(metric.get('network_in',0)),
            'network_out':float(metric.get('network_out',0)),
        })
    def get_features(self, server_id, current):
        buf = list(self._buf(server_id))
        cpu=float(current.get('cpu',0)); ram=float(current.get('ram',0))
        ni=float(current.get('network_in',0)); no=float(current.get('network_out',0))
        pr=float(current.get('processes',0))
        f = {'cpu_delta':0.0,'ram_delta':0.0,'disk_delta':0.0,'process_delta':0.0,
             'net_in_delta':0.0,'net_out_delta':0.0,
             'cpu_5min_avg':cpu,'ram_5min_avg':ram,'net_in_5min_avg':ni,'proc_5min_avg':pr}
        if not buf: return f
        p = buf[-1]
        f['cpu_delta']=cpu-p['cpu']; f['ram_delta']=ram-p['ram']
        f['disk_delta']=float(current.get('disk',0))-p['disk']
        f['process_delta']=pr-p['processes']
        f['net_in_delta']=ni-p['network_in']; f['net_out_delta']=no-p['network_out']
        n=len(buf)
        f['cpu_5min_avg']=sum(e['cpu'] for e in buf)/n
        f['ram_5min_avg']=sum(e['ram'] for e in buf)/n
        f['net_in_5min_avg']=sum(e['network_in'] for e in buf)/n
        f['proc_5min_avg']=sum(e['processes'] for e in buf)/n
        return f
    def consecutive_ram_increases(self, server_id):
        buf=list(self._buf(server_id))
        if len(buf)<2: return 0
        c=0
        for i in range(1,len(buf)):
            if buf[i]['ram']>buf[i-1]['ram']: c+=1
            else: c=0
        return c
    def buffer_size(self, server_id): return len(self._buf(server_id))

_engine = _TrendEngine()
def push_metric(server_id, metric): _engine.push(server_id, metric)
def get_trend_features(server_id, current): return _engine.get_features(server_id, current)
def get_consecutive_ram_increases(server_id): return _engine.consecutive_ram_increases(server_id)
def get_buffer_size(server_id): return _engine.buffer_size(server_id)
