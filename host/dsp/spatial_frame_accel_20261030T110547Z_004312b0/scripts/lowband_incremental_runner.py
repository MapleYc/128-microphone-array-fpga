"""Isolated causal WOLA runner for a frozen, separately supplied low-band core.

No source label, clean signal, true DOA, future trace or future sample is made
available to the estimator. Optional post-hoc T/I/Q replay is a separate API.
This module imports no candidate implementation and runs no experiment on import.
"""
import ast, math, copy
from pathlib import Path
from collections import OrderedDict
import numpy as np
from sprint_incremental_adapter import Incremental, N, HOP, FS
import sprint_blocked_power as blocked
from sprint_doa import select_channels

def config_for_lock_offset(cfg, physical_delta_deg):
 """Return a separate configuration for a test lock offset; never alter cfg.

 Physical and HOST azimuth signs are opposite. All three original configured
 beam angles move together; baseline output must be regenerated for this cfg.
 This function receives a test setting, not the unknown actual source bearing.
 """
 delta=float(physical_delta_deg)
 if not math.isfinite(delta) or abs(delta)>4.:raise ValueError('lock test delta must be finite and within +/-4 degrees')
 out=copy.deepcopy(cfg)
 for name in ('A','B1','B2'):
  az=float(out['directions'][name]['azimuth_deg'])-delta
  if abs(az)>90.:raise ValueError('lock perturbation exceeds HOST steering range')
  out['directions'][name]['azimuth_deg']=az
 return out

def static_candidate_check(path):
 """AST-only interface check. Does not import or execute candidate code."""
 tree=ast.parse(Path(path).read_text(encoding='utf8'),filename=str(path))
 classes=[x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='CovarianceLowBand']
 if len(classes)!=1:raise ValueError('Expected exactly one CovarianceLowBand class')
 methods={x.name:x for x in classes[0].body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))}
 expected={'prepare':['self','refs','lock','active'],'process':['self','S_epoch','old_gain','old_valid','active']}
 for name,args in expected.items():
  if name not in methods:raise ValueError('Missing method '+name)
  found=[a.arg for a in methods[name].args.args]
  if found[:len(args)]!=args:raise ValueError(f'{name} positional interface {found} != {args}')
 return {'class':'CovarianceLowBand','methods':expected,'proof_scope':'AST signature only, not algorithm, freeze, execution or safety validation'}

class LowBandIncremental(Incremental):
 """Add only estimator-enabled bins to the existing causal incremental path.

 estimator=None means old algorithm bitwise control. When non-None the caller
 MUST separately check frozen source/config hashes before construction; this
 wrapper does not decide if a candidate is authorized or scientifically valid.
 """
 def __init__(self,cfg,estimator=None,cache_capacity=128,record_gains=True,tracker_factory=None):
  super().__init__(cfg,cache_capacity=cache_capacity)
  if tracker_factory is not None:self.doa=tracker_factory(self.pos,self.lock)
  self.estimator=estimator;self.audit=[];self.gains=[];self.record_gains=bool(record_gains);self.extra_reference_frames=0
  hz=np.fft.rfftfreq(N,1/FS);self.modifiable=(hz>=150.)&(hz<500.)
 def _model(self,refs):
  key=tuple(round(float(x),4) for x in refs)
  if key not in self.cache:
   self.cache[key]=blocked.calibrate(list(key),self.cfg,self.beam)
   if len(self.cache)>self.cache_capacity:self.cache.popitem(last=False)
  else:self.cache.move_to_end(key)
  return self.cache[key]
 def _current(self):
  start=self.next_start;prior=self.decision;active=bool(prior and prior['valid']);Aframe=self._frame(start,self.lock);self.atraces.append(Aframe.copy());A=np.fft.rfft(Aframe*self.window);old=np.ones(N//2+1);old_valid=np.zeros(N//2+1,dtype=bool);refs=None;B=None
  if active:
   physical=float(prior['azimuth_deg']);side=1 if physical-self.lock>=0 else -1;other=physical+side*20
   if abs(other)>85:other=physical-side*20
   refs=[physical,float(other)];model=self._model(refs);B=np.array([np.fft.rfft(self._frame(start,b)*self.window) for b in refs]);Z=B-model['rT']*A;NI=model['scale']*np.sum(abs(Z)**2,axis=0);PA=abs(A)**2;clean=np.maximum(PA-NI,0.);g=np.divide(clean,clean+NI,out=np.ones(N//2+1),where=(clean+NI)>np.finfo(float).eps);old_valid=np.asarray(model['valid'],dtype=bool).copy();old[old_valid]=np.clip(g[old_valid],.1,1.)
  gain=old.copy();epoch=None;extra=False;log=None
  if self.estimator is not None:
   epoch=self.estimator.prepare(None if refs is None else list(refs),self.lock,active)
   epoch_active=bool(active and epoch is not None)
   if epoch_active:
    epoch=[float(x) for x in epoch]
    if len(epoch)!=2 or not all(math.isfinite(x) and abs(x)<=85. for x in epoch):raise ValueError('candidate epoch refs must be two finite physical front-sector angles')
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
   if not np.array_equal(proposed[protected],old[protected]):raise ValueError('candidate modified an old-valid or out-of-band gain')
   if not epoch_active and not np.array_equal(proposed,old):raise ValueError('inactive epoch must preserve old gain')
   gain=proposed
  y=np.fft.irfft(A*gain,n=N)*self.window;segment=y[:HOP]+self.tail;self.tail=y[HOP:];self.clip+=int(np.count_nonzero(abs(segment)>1));emitted=np.clip(segment,-1,1).astype('<f4')
  ix=np.mod(start+np.arange(N),self.capacity);observed=np.fft.rfft(self.ring[self.doa.channels[:,None],ix[None,:]].astype(float)*self.window,axis=1);self.decision=self.doa.process(observed);self.trace.append(self.decision.copy());self.next_start+=HOP
  self.audit.append(dict(frame=int(start//HOP),input_window_end=int(start+N),prior_active=active,test_only_forced_direction=bool(prior and prior.get('test_only_forced_direction',False)),old_refs=refs,epoch_refs=epoch,extra_reference_frame=extra,old_valid_bins=int(old_valid.sum()),changed_bins=np.flatnonzero(gain!=old).tolist(),mean_gain=float(np.mean(gain)),candidate_log=log))
  if self.record_gains:self.gains.append(gain.copy())
  return emitted

def fixed_direction_fault_factory(physical=38.0,valid_after_frame=0):
 """Explicit deterministic interface fault injection, never natural DOA.

 The constant is configured by the test operator and does not depend on any
 scene label, source data or future input. It can force the active branch on
 solo/silence cases. Record this mode and do not report it as DOA accuracy.
 """
 physical=float(physical);valid_after_frame=int(valid_after_frame)
 if not math.isfinite(physical) or abs(physical)>80 or valid_after_frame<0:raise ValueError('invalid forced direction schedule')
 def factory(positions,lock):
  if abs((physical-float(lock)+180)%360-180)<=10:raise ValueError('forced direction is inside target exclusion')
  class FixedTracker:
   def __init__(self):self.channels=select_channels(positions,count=24);self.frame=0
   def process(self,spectrum):
    if np.asarray(spectrum).shape!=(len(self.channels),N//2+1):raise ValueError('invalid observed spectrum shape')
    out=dict(frame=self.frame,valid=self.frame>=valid_after_frame,azimuth_deg=physical if self.frame>=valid_after_frame else None,test_only_forced_direction=True);self.frame+=1;return out
  return FixedTracker()
 return factory

def replay_sources_after_run(gains, components):
 """Evaluation only: apply already-frozen gains to A-beam T/I/Q arrays.

 Does not receive or invoke an estimator. Source arrays are never accessible
 from LowBandIncremental; compare their sum with actual unclipped output before
 using component scores. No nonlinear-post decomposition is implied.
 """
 g=np.asarray(gains,dtype=float);x=np.asarray(components,dtype=float)
 if g.ndim!=2 or g.shape[1]!=N//2+1 or x.ndim!=2:raise ValueError('invalid gain/component shape')
 if not np.isfinite(g).all() or not np.isfinite(x).all():raise ValueError('nonfinite evaluation input')
 if (len(g)-1)*HOP+N>x.shape[1]:raise ValueError('component streams too short')
 w=np.sqrt(.5-.5*np.cos(2*np.pi*np.arange(N)/N));tail=np.zeros((x.shape[0],HOP));out=[]
 for j,gain in enumerate(g):
  start=j*HOP;z=np.fft.irfft(np.fft.rfft(x[:,start:start+N]*w,axis=1)*gain[None,:],n=N,axis=1)*w;out.append(z[:,:HOP]+tail);tail=z[:,HOP:]
 return np.concatenate(out,axis=1) if out else np.empty((x.shape[0],0))
