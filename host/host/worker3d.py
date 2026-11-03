"""One real calibration worker, nonblocking audio-side mailbox operations."""
import copy,hashlib,json,threading,time
import native_paths
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
import numpy as np
import sprint_blocked_power as blocked
import sprint_doa_sector_guard as sector
import sprint_reference_guard as selector
import spatial_frame_accel as frame_accel
from lowband_guarded_runner import HOP
from calibrate3d import calibrate
from direction3d import host

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def geometry_version():
 source=Path(str(__import__('pathlib').Path(__file__).resolve().parent / '../dsp/stage2_20261029T154710Z_63ffc32c/adapter/config/geometry_source.yaml'))
 raw=dict(geometry_sha=sha(source),geometry_implementation_sha=sha(blocked.geom.__file__),mask=list(blocked.geom.MASK),fs=blocked.FS,n=blocked.N,hop=HOP)
 return hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()
def version_for(cfg):
 root=Path(__file__).parent
 raw=dict(cfg=cfg,geometry=geometry_version(),original_parameters=blocked.PARAMETERS,local={n:sha(root/n) for n in ('direction3d.py','doa3d.py','calibrate3d.py','machine3d.py','worker3d.py','runtime3d.py')},frame_dll_sha=sha(native_paths.frame_library()))
 return hashlib.sha256(json.dumps(raw,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

@dataclass(frozen=True)
class Payload:
 seq:int;epoch:int;version:str;lock:float;pair:tuple;model:object;taps:tuple

_BODY_CACHE=OrderedDict()
_BODY_LOCK=threading.Lock()
_BODY_CACHE_CAPACITY=16

def build_payload(job,cfg,pos):
 if version_for(cfg)!=job.version:raise RuntimeError('frozen version changed before build')
 key=(job.version,job.lock,job.pair)
 # Geometry/calibration/taps depend only on this immutable configuration and
 # direction pair, not on audio, wall time, frame number or source labels.
 # Reuse only a body actually computed earlier; every publication still gets
 # the current job's identity, observed evidence timestamps and expiry checks.
 with _BODY_LOCK:
  body=_BODY_CACHE.get(key)
  if body is not None:_BODY_CACHE.move_to_end(key)
 if body is not None:return Payload(job.seq,job.epoch,job.version,job.lock,job.pair,body[0],body[1])
 model=calibrate(list(job.pair),cfg,None)
 r=np.asarray(model['rT']);scale=np.asarray(model['scale']);valid=np.asarray(model['valid'])
 if r.shape!=(2,513) or scale.shape!=(513,) or valid.shape!=(513,) or valid.dtype!=np.bool_ or not np.isfinite(r).all() or not np.isfinite(scale[valid]).all():raise ValueError('invalid calibration output')
 use=np.array([m for m in range(128) if m not in blocked.geom.MASK],dtype=np.int64);use.setflags(write=False)
 taps=[]
 for physical in job.pair:
  d=host(physical);ii,cc,_=blocked.geom._taps(pos,d,blocked.FS)
  delay=np.ascontiguousarray(ii[use],dtype=np.int64);coeff=np.ascontiguousarray(cc[use],dtype=np.float64);delay.setflags(write=False);coeff.setflags(write=False);taps.append((use,delay,coeff))
 for x in model.values():
  if isinstance(x,np.ndarray):x.setflags(write=False)
 if version_for(cfg)!=job.version:raise RuntimeError('frozen version changed during build')
 body=(MappingProxyType(model),tuple(taps))
 with _BODY_LOCK:
  _BODY_CACHE[key]=body;_BODY_CACHE.move_to_end(key)
  while len(_BODY_CACHE)>_BODY_CACHE_CAPACITY:_BODY_CACHE.popitem(last=False)
 return Payload(job.seq,job.epoch,job.version,job.lock,job.pair,body[0],body[1])

class SnapshotWorker:
 def __init__(self,builder=build_payload):
  self._builder=builder;self._lock=threading.Lock();self._slot=None;self._done=None;self._closing=False
  self._thread=threading.Thread(target=self._run,name='snapshot-model-worker',daemon=True);self._thread.start()
 def try_submit(self,job,cfg,pos):
  if self._closing:return False
  if not self._lock.acquire(False):return False
  try:
   if self._slot is not None or self._done is not None:return False
   self._slot=(job,cfg,pos);return True
  finally:self._lock.release()
 def poll(self):
  if not self._lock.acquire(False):return []
  try:
   x=self._done;self._done=None;return [] if x is None else [x]
  finally:self._lock.release()
 def close(self):self._closing=True
 def wait_closed(self,timeout=None):self._thread.join(timeout);return not self._thread.is_alive()
 def alive(self):return self._thread.is_alive()
 def _run(self):
  while True:
   with self._lock:
    task=self._slot;self._slot=None
    if self._closing and task is None:return
   if task is None:
    time.sleep(.001);continue
   job,cfg,pos=task
   if self._closing:payload=None;error='shutdown_before_start'
   else:
    try:payload=self._builder(job,cfg,pos);error=None
    except Exception as exc:payload=None;error=(type(exc).__name__+': '+str(exc))[:300]
   done_ns=time.perf_counter_ns()
   with self._lock:
    if self._done is not None:raise RuntimeError('unconsumed completion violates one-inflight contract')
    self._done=(job,payload,error,done_ns)
   if self._closing:return
