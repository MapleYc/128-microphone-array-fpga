"""Incremental guarded spatial output, one frame delivered at a time.

Default operation is the original blocked-power model plus sector/B2 guards.
NNLS is an optional, explicitly enabled software experiment. No post-processing
is implemented here. No input source labels, future DOA or future samples enter
the algorithm. Callers own input arrays until the iterator consumes/releases them.
"""
import importlib, copy
import math
import numpy as np
from lowband_guarded_runner import GuardedLowBandIncremental, N, HOP, FS

_RELATIVE_FLOOR=10.0**(-3.0/20.0)

class _RelativeFloorEstimator:
 """Limit each proposed gain relative to this same frame's original gain."""
 def __init__(self,core):self._core=core
 def prepare(self,refs,lock,active):return self._core.prepare(refs,lock,active)
 def process(self,S_epoch,old_gain,old_valid,active):
  if np.iscomplexobj(old_gain):raise ValueError('experimental old gain must be real')
  before=np.asarray(old_gain,dtype=float).copy();valid=np.asarray(old_valid)
  if before.shape!=(N//2+1,) or valid.shape!=before.shape or valid.dtype!=np.bool_ or not np.isfinite(before).all():raise ValueError('experimental old-gain/valid input contract')
  if not np.array_equal(before[4:11][~valid[4:11]],np.ones(np.count_nonzero(~valid[4:11]))):raise ValueError('old-invalid low-frequency gain must be unity')
  proposed,log=self._core.process(S_epoch,before.copy(),valid.copy(),active)
  if np.iscomplexobj(proposed):raise ValueError('experimental gain must be real')
  proposed=np.asarray(proposed,dtype=float)
  if proposed.shape!=before.shape or not np.isfinite(proposed).all():raise ValueError('experimental gain shape/nonfinite')
  limited=proposed<before*_RELATIVE_FLOOR
  result=np.maximum(proposed,before*_RELATIVE_FLOOR)
  # Preserve per-bin audit consistency if the wrapper enforces a bound.
  if isinstance(log,dict):
   log=dict(log)
   if 'bin_records' in log:
    records=[]
    for item in log['bin_records']:
     row=dict(item);k=int(row['bin'])
     if limited[k]:row['uncapped_gain']=float(proposed[k]);row['gain']=float(result[k]);row['relative_floor_applied']=True
     records.append(row)
    log['bin_records']=records
   log['relative_3dB_floor_clamped_bins']=np.flatnonzero(limited).tolist()
   log['relative_floor_scope']='same frame old gain, not previous frame'
  return result,log

class _PacketIterator:
 """Single owner of an input packet; callers can query consumed before close."""
 def __init__(self,owner,packet):self._owner=owner;self._packet=packet;self._length=packet.shape[1];self._at=0;self._done=False
 @property
 def consumed_samples(self):return self._at
 @property
 def remaining_samples(self):return self._length-self._at
 def __iter__(self):return self
 def _release(self):
  if not self._done:
   self._done=True;self._owner._packet_consumed=self._at;self._owner._active=None;self._packet=None
 def close(self):
  """Release unconsumed suffix untouched; return consumed/remaining counts."""
  before=self.remaining_samples;self._release();return {'consumed_samples':self._at,'remaining_samples':before}
 def __next__(self):
  if self._done:raise StopIteration
  s=self._owner;e=s._engine
  try:
   while self._at<self._length:
    need=e.next_start+N-e.received
    if need<=0:raise RuntimeError('invalid input/frame progress')
    take=min(need,self._length-self._at)
    # Only this slice is inspected. A later suffix, including NaNs, remains
    # completely unread until the caller requests another next().
    chunk=self._packet[:,self._at:self._at+take]
    if not np.isfinite(chunk).all():raise ValueError('nonfinite sample in consumed input slice')
    offset=e.received%e.capacity;first=min(take,e.capacity-offset)
    e.ring[:,offset:offset+first]=chunk[:,:first]
    if first<take:e.ring[:,:take-first]=chunk[:,first:]
    self._at+=take;e.received+=take;s._packet_consumed=self._at
    if e.received==e.next_start+N:
     samples=e._current();audit=e.audit.pop();decision=e.trace.pop();e.atraces.clear()
     if e.audit or e.trace or e.gains:raise RuntimeError('internal frame records must be drained')
     frame=audit['frame'];begin=frame*HOP;end=begin+HOP;window_end=frame*HOP+N
     if e.received!=window_end or len(samples)!=HOP:raise RuntimeError('unexpected completed-frame boundary')
     event={'samples':samples,'frame_index':frame,'packet_consumed_samples':self._at,'input_consumed_total_samples':e.received,'input_window_start_sample':frame*HOP,'input_window_end_exclusive':window_end,'input_window_end_seconds':window_end/FS,'output_start_sample':begin,'output_end_exclusive':end,'output_start_seconds':begin/FS,'output_end_seconds':end/FS,'first_output_sample_availability_delay_samples':window_end-begin,'last_output_sample_availability_delay_samples':window_end-(end-1),'audit':audit,'next_doa_decision':decision}
     if self._at==self._length:self._release()
     return event
   self._release();raise StopIteration
  except StopIteration:raise
  except Exception:
   # The exact consumed prefix stays queryable. A failed stream must be reset;
   # silently skipping malformed input or resuming partial DSP state is unsafe.
   self._release();s._failed=True;raise

class SpatialFrameStream:
 """Bounded single-consumer guarded spatial stream.

 push_iter accepts only a 128 x N numpy float32 array. The caller must consume
 or close its iterator before pushing another packet. No full-packet copy or
 validation occurs. Multiple threads must not use one stream concurrently.
 finish emits no remaining WOLA tail; post chain is not implemented.
 """
 def __init__(self,cfg,*,enable_nnls=False,cache_capacity=128):
  if not isinstance(enable_nnls,bool):raise ValueError('enable_nnls must be an explicit boolean')
  if isinstance(cache_capacity,bool) or not isinstance(cache_capacity,int) or not 1<=cache_capacity<=128:raise ValueError('cache_capacity must be integer in 1..128')
  self._cfg=copy.deepcopy(cfg);self._nnls=enable_nnls;self._capacity=cache_capacity;self._active=None;self._packet_consumed=0;self._closed=False;self._failed=False;self._make_engine()
 def _make_engine(self):
  estimator=None
  if self._nnls:
   # Import/initialize NNLS only after the caller explicitly enables it.
   estimator=_RelativeFloorEstimator(importlib.import_module('lowband_cov_nnls').CovarianceLowBand())
  self._engine=GuardedLowBandIncremental(self._cfg,estimator=estimator,cache_capacity=self._capacity,record_gains=False)
 def push_iter(self,decoded):
  if self._closed or self._failed:raise RuntimeError('stream closed or failed; reset before reuse')
  if self._active is not None:raise RuntimeError('consume or close the active packet first')
  if not isinstance(decoded,np.ndarray) or decoded.ndim!=2 or decoded.shape[0]!=128 or decoded.dtype!=np.float32:raise ValueError('packet must be a 128 x N numpy float32 array')
  self._packet_consumed=0;self._active=_PacketIterator(self,decoded);return self._active
 @property
 def input_consumed_total_samples(self):return self._engine.received
 @property
 def last_packet_consumed_samples(self):return self._packet_consumed
 def cancel_packet(self):
  """Release an abandoned active iterator and return its consumed/remaining counts."""
  return None if self._active is None else self._active.close()
 def stats(self):
  e=self._engine
  return {'input_consumed_total_samples':e.received,'next_frame_start_sample':e.next_start,'frames_emitted':e.next_start//HOP,'packet_consumed_samples':self._packet_consumed,'packet_active':self._active is not None,'failed':self._failed,'closed':self._closed,'nnls_enabled':self._nnls,'ring_samples_per_channel':e.capacity,'ring_bytes':e.ring.nbytes,'old_calibration_cache_items':len(e.cache),'old_calibration_cache_capacity':e.cache_capacity,'tap_cache_items':len(e.beam.taps),'tap_cache_capacity':e.taps_capacity,'extra_epoch_reference_frames':e.extra_reference_frames,'spatial_clipped_samples':e.clip,'pending_internal_audit_records':len(e.audit),'pending_internal_trace_records':len(e.trace),'pending_internal_gain_records':len(e.gains)}
 def finish(self):
  if self._active is not None:raise RuntimeError('consume or close the active packet before finish')
  if self._closed or self._failed:raise RuntimeError('stream already closed or failed')
  e=self._engine;frames=e.next_start//HOP;last_covered=(e.next_start-HOP+N) if frames else 0;self._closed=True
  return {'input_consumed_total_samples':e.received,'frames_emitted':frames,'output_samples':frames*HOP,'input_after_last_complete_window_samples':e.received-last_covered,'unflushed_WOLA_tail_samples':HOP if frames else 0,'flush_performed':False,'post_processing':'not implemented in streaming adapter'}
 def reset(self):
  if self._active is not None:raise RuntimeError('consume or close the active packet before reset')
  self._closed=False;self._failed=False;self._packet_consumed=0;self._make_engine()
  return self.stats()
