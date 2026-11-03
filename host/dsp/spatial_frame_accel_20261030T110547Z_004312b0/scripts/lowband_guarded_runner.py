"""Independent sector/B2 guarded causal low-band research runner.

Natural tracking uses the frozen sector DOA implementation. Every applied
reference pair, including a held low-band epoch, is validated against the locked
target and front-sector limit. No source identity, simulated true DOA or future
samples enter either guard or the low-band estimator. This is software research,
not an RTL, firmware, acoustic or hardware validation.
"""
import math
import numpy as np
import sprint_doa_sector_guard as sector
import sprint_reference_guard as reference_guard
from lowband_incremental_runner import LowBandIncremental, N, HOP, FS

def legal_reference_pair(refs,lock):
 """Use the frozen reference selector's bounds, exclusion and arc semantics."""
 if refs is None:return False
 try:
  if len(refs)!=2:return False
  a,b=(float(x) for x in refs);lock=float(lock)
 except (TypeError,ValueError,OverflowError):return False
 return (math.isfinite(a) and math.isfinite(b) and math.isfinite(lock) and
         abs(a)<=reference_guard.VIEW_EDGE_DEG and abs(b)<=reference_guard.VIEW_EDGE_DEG and
         sector.outside_target(a,lock,reference_guard.EXCLUSION_DEG) and
         sector.outside_target(b,lock,reference_guard.EXCLUSION_DEG) and
         abs(sector.wrapped_delta(a,b))>reference_guard.MIN_SEPARATION_DEG and
         sector.clear_short_arc(a,b,lock,reference_guard.EXCLUSION_DEG))

class GuardedLowBandIncremental(LowBandIncremental):
 """Use sector+B2 guards; any invalid pair makes old and new gains unity.

 tracker_factory is deliberately test-only: caller must set
 allow_test_tracker=True and mark injected test results; natural mode never
 permits an accidental override of the frozen sector tracker.
 """
 def __init__(self,cfg,estimator=None,cache_capacity=128,record_gains=True,tracker_factory=None,allow_test_tracker=False):
  if tracker_factory is not None and not allow_test_tracker:raise ValueError('tracker override is test-only and must be explicitly enabled')
  factory=tracker_factory if tracker_factory is not None else lambda positions,lock:sector.InterferenceDOA(positions,lock_physical_az=lock)
  super().__init__(cfg,estimator=estimator,cache_capacity=cache_capacity,record_gains=record_gains,tracker_factory=factory)
  self.test_tracker_override=tracker_factory is not None

 def _current(self):
  start=self.next_start;prior=self.decision;frame=int(start//HOP);decision_frame=None if prior is None else prior.get('frame')
  if prior is not None and decision_frame!=frame-1:raise ValueError('DOA decision must be from the immediately preceding frame')
  tracker_valid=bool(prior and prior['valid']);physical=prior.get('azimuth_deg') if tracker_valid else None
  pair_info=reference_guard.select_reference_pair_info(physical,self.lock) if tracker_valid else {'refs':None,'reason':'tracker_invalid'}
  current_pair=pair_info['refs'];active=bool(tracker_valid and legal_reference_pair(current_pair,self.lock));pair_reason=pair_info['reason'] if active or not tracker_valid or current_pair is None else 'selector_returned_invalid_pair'
  refs=list(current_pair) if active else None;Aframe=self._frame(start,self.lock);self.atraces.append(Aframe.copy());A=np.fft.rfft(Aframe*self.window);old=np.ones(N//2+1);old_valid=np.zeros(N//2+1,dtype=bool);B=None
  if active:
   model=self._model(refs);B=np.array([np.fft.rfft(self._frame(start,b)*self.window) for b in refs]);Z=B-model['rT']*A;NI=model['scale']*np.sum(abs(Z)**2,axis=0);PA=abs(A)**2;clean=np.maximum(PA-NI,0.);g=np.divide(clean,clean+NI,out=np.ones(N//2+1),where=(clean+NI)>np.finfo(float).eps);old_valid=np.asarray(model['valid'],dtype=bool).copy();old[old_valid]=np.clip(g[old_valid],.1,1.)
  gain=old.copy();epoch=None;extra=False;log=None;epoch_rejected=False
  if self.estimator is not None:
   epoch=self.estimator.prepare(None if refs is None else list(refs),self.lock,active)
   epoch_active=bool(active and epoch is not None)
   if epoch_active and not legal_reference_pair(epoch,self.lock):
    # Do not keep the old filter active if the epoch pair is unsafe: both
    # paths return to A, and the core is informed immediately to reset.
    epoch_rejected=True;active=False;refs=None;epoch=None;old=np.ones(N//2+1);old_valid=np.zeros(N//2+1,dtype=bool);self.estimator.prepare(None,self.lock,False);epoch_active=False;pair_reason='invalid_epoch_both_unity'
   if epoch_active:
    epoch=[float(x) for x in epoch]
    if epoch==refs:epoch_B=B
    else:
     epoch_B=np.array([np.fft.rfft(self._frame(start,b)*self.window) for b in epoch]);self.extra_reference_frames+=1;extra=True
   else:epoch_B=np.zeros((2,N//2+1),dtype=np.complex128)
   S=np.vstack((A[None,:],epoch_B));proposed,log=self.estimator.process(S,old.copy(),old_valid.copy(),epoch_active)
   if np.iscomplexobj(proposed):raise ValueError('candidate gain must be real, not complex')
   proposed=np.asarray(proposed,dtype=float)
   if proposed.shape!=(N//2+1,) or not np.isfinite(proposed).all():raise ValueError('candidate gain must be 513 finite real values')
   if np.any(proposed<.1-1e-12) or np.any(proposed>1.+1e-12):raise ValueError('candidate gain outside frozen current range')
   protected=old_valid | ~self.modifiable
   if not np.array_equal(proposed[protected],old[protected]):raise ValueError('candidate modified old-valid or out-of-band gain')
   if not epoch_active and not np.array_equal(proposed,old):raise ValueError('inactive epoch must preserve old gain')
   gain=proposed
  # A unity frame participates in the original WOLA overlap; the preceding
  # frame's synthesis tail remains and is not claimed to vanish immediately.
  y=np.fft.irfft(A*gain,n=N)*self.window;segment=y[:HOP]+self.tail;self.tail=y[HOP:];self.clip+=int(np.count_nonzero(abs(segment)>1));emitted=np.clip(segment,-1,1).astype('<f4')
  ix=np.mod(start+np.arange(N),self.capacity);observed=np.fft.rfft(self.ring[self.doa.channels[:,None],ix[None,:]].astype(float)*self.window,axis=1);self.decision=self.doa.process(observed);self.trace.append(self.decision.copy());self.next_start+=HOP
  self.audit.append(dict(frame=frame,input_window_end=int(start+N),applied_decision_frame=decision_frame,apply_only_next_hop=True,tracker_prior_valid=tracker_valid,tracker_prior_physical=physical,application_active=active,prior_active=active,test_only_forced_direction=bool(prior and prior.get('test_only_forced_direction',False)),test_tracker_override=self.test_tracker_override,guard_pair_reason=pair_reason,selected_pair=current_pair,old_refs=refs,epoch_refs=epoch,epoch_rejected=epoch_rejected,old_gain_unity=bool(np.array_equal(old,np.ones(N//2+1))),extra_reference_frame=extra,old_valid_bins=int(old_valid.sum()),changed_bins=np.flatnonzero(gain!=old).tolist(),mean_gain=float(np.mean(gain)),candidate_log=log))
  if self.record_gains:self.gains.append(gain.copy())
  return emitted
