"""Causal atomic spatial snapshot trial; original DSP/post otherwise unchanged."""
import copy,time,types
import numpy as np
from spatial_frame_accel import AcceleratedSpatialFrameStream,_call
import sprint_reference_guard as reference_guard
import sprint_doa_sector_guard as sector
from lowband_guarded_runner import legal_reference_pair,N,HOP
from snapshot_machine import SnapshotMachine,decision_fresh
from snapshot_worker import SnapshotWorker,Payload,build_payload,version_for

def _materialize(job,payload):
 # Full arrays and taps were built/validated in the worker. This owner-thread
 # check is constant-size and publication only swaps an immutable reference.
 if not isinstance(payload,Payload) or (payload.seq,payload.epoch,payload.version,payload.lock,payload.pair)!=(job.seq,job.epoch,job.version,job.lock,job.pair):raise ValueError('snapshot identity mismatch')
 return payload

def _frame_taps(engine,start,taps):
 use,delay,coeff=taps
 for tap in range(4):
  first=start-delay-tap+1;last=first+N-1;valid=last>=0
  if np.any(valid & (last>=engine.received)):raise RuntimeError('future sample requested')
  if np.any(valid & (np.maximum(first,0)<engine.received-engine.capacity)):raise RuntimeError('ring history overwritten')
 out=np.empty(N,dtype='<f4');_call(engine.ring.ctypes.data,engine.capacity,use.ctypes.data,delay.ctypes.data,coeff.ctypes.data,len(use),int(start),out.ctypes.data)
 return out

def _current(engine):
 start=engine.next_start;prior=engine.decision;frame=int(start//HOP);decision_frame=None if prior is None else prior.get('frame')
 if prior is not None and decision_frame!=frame-1:raise ValueError('DOA decision must be from immediately preceding frame')
 tracker_valid=bool(prior and prior['valid']);physical=prior.get('azimuth_deg') if tracker_valid else None
 info=reference_guard.select_reference_pair_info(physical,engine.lock) if tracker_valid else {'refs':None,'reason':'tracker_invalid'}
 pair=info['refs'];legal=bool(tracker_valid and legal_reference_pair(pair,engine.lock) and not engine.snapshot_paused)
 # Poll mailbox before now_ns so a consumed completion cannot have a future timestamp.
 completions=engine.worker.poll();now=time.perf_counter_ns();prior_ns_used=engine.prior_ns;evidence_frame_used=engine.evidence_frame;evidence_ns_used=engine.evidence_ns
 state=engine.machine.advance(frame,now,pair if legal else None,decision_frame,prior_ns_used,evidence_frame_used,evidence_ns_used,completions)
 job=engine.machine.peek_pending()
 if job is not None and engine.worker.try_submit(job,engine.worker_cfg,engine.worker_pos):engine.machine.mark_started(job)
 snapshot=state['active'];payload=None if snapshot is None else snapshot.payload
 Aframe=engine._frame(start,engine.lock);engine.atraces.append(Aframe.copy());A=np.fft.rfft(Aframe*engine.window);gain=np.ones(N//2+1);valid=np.zeros(N//2+1,dtype=bool)
 if payload is not None:
  model=payload.model;B=np.array([np.fft.rfft(_frame_taps(engine,start,t)*engine.window) for t in payload.taps]);Z=B-model['rT']*A;NI=model['scale']*np.sum(abs(Z)**2,axis=0);PA=abs(A)**2;clean=np.maximum(PA-NI,0.);g=np.divide(clean,clean+NI,out=np.ones(N//2+1),where=(clean+NI)>np.finfo(float).eps);valid=np.asarray(model['valid'],dtype=bool).copy();gain[valid]=np.clip(g[valid],.1,1.)
 y=np.fft.irfft(A*gain,n=N)*engine.window;segment=y[:HOP]+engine.tail;engine.tail=y[HOP:];engine.clip+=int(np.count_nonzero(abs(segment)>1));emitted=np.clip(segment,-1,1).astype('<f4')
 ix=np.mod(start+np.arange(N),engine.capacity);observed=np.fft.rfft(engine.ring[engine.doa.channels[:,None],ix[None,:]].astype(float)*engine.window,axis=1);decision=engine.doa.process(observed);observed_ns=time.perf_counter_ns();engine.decision=decision;engine.prior_ns=observed_ns
 fresh=decision_fresh(decision,engine.lock)
 if fresh:engine.evidence_frame=decision['frame'];engine.evidence_ns=observed_ns
 elif not decision['valid']:engine.evidence_frame=None;engine.evidence_ns=None
 engine.trace.append(decision.copy());engine.next_start+=HOP
 applied=None if payload is None else list(payload.pair)
 audit=dict(frame=frame,input_window_end=int(start+N),applied_decision_frame=decision_frame,decision_observed_ns=prior_ns_used,next_decision_observed_ns=observed_ns,frame_policy_now_ns=now,tracker_prior_valid=tracker_valid,tracker_prior_physical=physical,selected_pair=pair,guard_pair_reason=info['reason'],application_active=payload is not None,old_refs=applied,old_gain_unity=bool(np.array_equal(gain,np.ones(N//2+1))),old_valid_bins=int(valid.sum()),mean_gain=float(np.mean(gain)),snapshot_status=state['status'],snapshot_seq=None if snapshot is None else snapshot.job.seq,snapshot_source_frame=None if snapshot is None else snapshot.job.source_frame,snapshot_source_ns=None if snapshot is None else snapshot.job.source_ns,snapshot_evidence_frame=None if snapshot is None else snapshot.job.evidence_frame,snapshot_evidence_ns=None if snapshot is None else snapshot.job.evidence_ns,snapshot_version=None if snapshot is None else snapshot.job.version,snapshot_lock=None if snapshot is None else snapshot.job.lock,snapshot_delta_deg=None if payload is None else [abs(sector.wrapped_delta(a,b)) for a,b in zip(payload.pair,pair)],policy_evidence_frame=evidence_frame_used,policy_evidence_ns=evidence_ns_used,next_evidence_frame=engine.evidence_frame,next_evidence_ns=engine.evidence_ns,next_decision_fresh=fresh,snapshot_epoch=engine.machine.epoch,snapshot_events=engine.machine.drain_events(),snapshot_event_overflow_total=engine.machine.event_overflow,wola_previous_tail_retained=True,test_only_builder=engine.test_only_builder)
 if engine.include_gain:audit['gain_vector']=gain.copy()
 engine.audit.append(audit)
 if engine.record_gains:engine.gains.append(gain.copy())
 return emitted

class AtomicSnapshotSpatialStream(AcceleratedSpatialFrameStream):
 def __init__(self,cfg,*,include_gain=False,builder=None,allow_test_builder=False):
  if builder is not None and not allow_test_builder:raise ValueError('custom builder requires explicit test-only flag')
  self._worker_cfg=copy.deepcopy(cfg);self._version=version_for(self._worker_cfg);self._machine=SnapshotMachine(-float(cfg['directions']['A']['azimuth_deg']),self._version,_materialize);self._worker=SnapshotWorker(builder or build_payload)
  self._paused=False;self._include_gain=bool(include_gain);self._test_only_builder=builder is not None
  try:super().__init__(cfg,enable_nnls=False)
  except BaseException:self._worker.close();raise
 def _make_engine(self):
  super()._make_engine();e=self._engine;e.machine=self._machine;e.worker=self._worker;e.worker_cfg=self._worker_cfg;e.worker_pos=np.array(e.beam.pos,dtype=np.float64,copy=True);e.worker_pos.setflags(write=False);e.prior_ns=None;e.evidence_frame=None;e.evidence_ns=None;e.snapshot_paused=self._paused;e.include_gain=self._include_gain;e.test_only_builder=self._test_only_builder;e._current=types.MethodType(_current,e)
 def reset(self):
  if self._active is not None:raise RuntimeError('consume or close active packet before reset')
  self._worker_cfg=copy.deepcopy(self._cfg);self._version=version_for(self._worker_cfg);self._machine.invalidate('reset',new_lock=-float(self._cfg['directions']['A']['azimuth_deg']),new_version=self._version,reset_frame=True)
  return super().reset()
 def set_target_config(self,cfg):
  if self._active is not None:raise RuntimeError('consume or close active packet before reconfiguration')
  version_for(cfg);self._cfg=copy.deepcopy(cfg);return self.reset()
 def pause_model(self):self._paused=True;self._engine.snapshot_paused=True;self._machine.invalidate('pause')
 def resume_model(self):self._paused=False;self._engine.snapshot_paused=False
 def finish(self):
  result=super().finish();self._machine.invalidate('finish');return result
 def drain_control_events(self):
  # Call outside the audio callback, including after waiting for shutdown.
  for job,payload,error,done_ns in self._worker.poll():
   self._machine._log('completed','control_drain',job,completed_ns=done_ns,error=error)
   if self._machine.inflight is job:self._machine.inflight=None
   self._machine._log('rejected','control_drain_no_audio_frame',job)
  return {'events':self._machine.drain_events(),'event_overflow_total':self._machine.event_overflow,'worker_alive':self._worker.alive()}
 def shutdown(self):self._machine.invalidate('shutdown');self._worker.close()
 def wait_shutdown(self,timeout=None):return self._worker.wait_closed(timeout)
