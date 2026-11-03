"""Causal atomic-reference snapshot policy; no clock or worker of its own.

One owner calls advance() once per frame, supplies monotonic timestamps and
zero or more observed worker completions. The identical state machine supports
deterministic event simulation. 1 degree / 24 hops / 256 ms are trial limits,
not an acoustic safety proof.
"""
from collections import deque
from dataclasses import dataclass
import math
from direction3d import direction,angle,arc_clear,legal_pair as legal_reference_pair

MAX_DELTA_DEG=1.0;MAX_HOPS=24;MAX_WALL_NS=256_000_000;REFRESH_HOPS=16;LOG_CAPACITY=512

def decision_fresh(decision,lock):
 raw=(decision.get('raw_candidate_azimuth_deg'),decision.get('raw_candidate_elevation_deg'))
 held=(decision.get('azimuth_deg'),decision.get('elevation_deg'))
 try:return bool(decision.get('valid',False) and decision.get('evidence_valid',False) and decision.get('pending_confirmations',0)>=3 and angle(raw,held)<=MAX_DELTA_DEG and arc_clear(raw,held,lock))
 except (ValueError,TypeError):return False

@dataclass
class Job:
 seq:int;epoch:int;version:str;lock:float;pair:tuple;source_frame:int;source_ns:int;evidence_frame:int;evidence_ns:int
 revoked:str=''

@dataclass(frozen=True)
class Snapshot:
 job:Job;payload:object

class SnapshotMachine:
 def __init__(self,lock,version,materialize):
  self.lock=direction(lock);self.version=str(version);self.materialize=materialize;self.epoch=0;self.sequence=0
  self.active=None;self.inflight=None;self.pending=None;self.latest=None;self.last_frame=None;self.last_ns=None;self.invalid=False
  self._events=deque(maxlen=LOG_CAPACITY);self.event_overflow=0
 def _log(self,kind,reason='',job=None,**extra):
  item={'event':kind,'reason':reason,'frame':self.last_frame,'observed_ns':self.last_ns,'epoch':self.epoch}
  if job is not None:item.update(seq=job.seq,job_epoch=job.epoch,version=job.version,lock=job.lock,pair=list(job.pair),source_frame=job.source_frame,source_ns=job.source_ns,evidence_frame=job.evidence_frame,evidence_ns=job.evidence_ns)
  item.update(extra)
  if len(self._events)==LOG_CAPACITY:self.event_overflow+=1
  self._events.append(item)
 def drain_events(self):
  out=list(self._events);self._events.clear();return out
 def _legal(self,pair):return pair is not None and legal_reference_pair(pair,self.lock)
 def _within(self,pair,other):
  if not self._legal(pair) or not self._legal(other):return False
  return all(angle(a,b)<=MAX_DELTA_DEG and arc_clear(a,b,self.lock,10.) for a,b in zip(pair,other))
 def _fresh(self,frame,now,src_frame,src_ns):
  return 0<=frame-src_frame<=MAX_HOPS and 0<=now-src_ns<=MAX_WALL_NS
 def _valid_job(self,job,frame,now,current,current_key):
  return (not job.revoked and job.epoch==self.epoch and job.version==self.version and job.lock==self.lock and
          self._within(job.pair,current) and self._within(job.pair,current_key) and self._fresh(frame,now,job.source_frame,job.source_ns) and
          self._fresh(frame,now,job.evidence_frame,job.evidence_ns))
 def _revoke(self,job,reason):
  if job is not None and not job.revoked:job.revoked=reason;self._log('rejected',reason,job)
 def invalidate(self,reason,new_lock=None,new_version=None,reset_frame=False):
  self.epoch+=1
  if self.active is not None:self._log('rejected',reason,self.active.job);self.active=None
  self._revoke(self.inflight,reason)
  if self.pending is not None:self._log('superseded',reason,self.pending);self.pending=None
  self.latest=None;self.invalid=True
  if new_lock is not None:self.lock=direction(new_lock)
  if new_version is not None:self.version=str(new_version)
  if reset_frame:self.last_frame=None
 def _new_pending(self,pair,prior_frame,prior_ns,evidence_frame,evidence_ns):
  if self.pending is not None:self._log('superseded','newer_proposal',self.pending)
  self.sequence+=1;job=Job(self.sequence,self.epoch,self.version,self.lock,pair,prior_frame,prior_ns,evidence_frame,evidence_ns);self.pending=job;self._log('issued','latest',job)
 def peek_pending(self):return self.pending if self.inflight is None else None
 def mark_started(self,job):
  if self.inflight is not None or self.pending is not job:raise RuntimeError('invalid job start')
  self.pending=None;self.inflight=job;self._log('started','worker',job)
 def advance(self,frame,now_ns,pair,prior_frame,prior_ns,evidence_frame,evidence_ns,completions=()):
  frame=int(frame);now_ns=int(now_ns)
  if (self.last_frame is not None and frame<=self.last_frame) or (self.last_ns is not None and now_ns<self.last_ns):raise ValueError('nonmonotonic frame/clock')
  self.last_frame=frame;self.last_ns=now_ns
  raw_pair=None if pair is None else tuple(direction(x) for x in pair)
  pair=None if raw_pair is None else tuple(tuple(round(v,4) for v in x) for x in raw_pair)
  legal=(self._legal(raw_pair) and self._legal(pair) and prior_frame is not None and prior_ns is not None and evidence_frame is not None and evidence_ns is not None and
         int(prior_frame)==frame-1 and int(evidence_frame)<=int(prior_frame) and int(evidence_ns)<=int(prior_ns) and
         self._fresh(frame,now_ns,int(prior_frame),int(prior_ns)) and self._fresh(frame,now_ns,int(evidence_frame),int(evidence_ns)))
  if not legal:
   if not self.invalid:self.invalidate('invalid_or_stale_evidence')
   self.latest=None
  else:
   self.invalid=False;self.latest=pair
   if self.active is not None and not self._valid_job(self.active.job,frame,now_ns,raw_pair,pair):self._log('rejected','active_age_angle_or_guard',self.active.job);self.active=None
   if self.inflight is not None and not self._valid_job(self.inflight,frame,now_ns,raw_pair,pair):self._revoke(self.inflight,'inflight_age_angle_or_guard')
   if self.pending is not None and not self._valid_job(self.pending,frame,now_ns,raw_pair,pair):self._log('superseded','pending_age_angle_or_guard',self.pending);self.pending=None
  for job,model,error,done_ns in completions:
   self._log('completed','worker_error' if error else 'worker_done',job,completed_ns=int(done_ns),error=error)
   if self.inflight is not job:self._log('rejected','unknown_completion',job);continue
   self.inflight=None
   if error:self._log('rejected','worker_error',job);continue
   if not job.source_ns<=int(done_ns)<=now_ns:self._log('rejected','invalid_completion_time',job);continue
   if not legal or not self._valid_job(job,frame,now_ns,raw_pair,pair):self._log('rejected',job.revoked or 'completion_age_angle_or_guard',job);continue
   if self.active is not None and job.seq<=self.active.job.seq:self._log('rejected','sequence_rollback',job);continue
   try:payload=self.materialize(job,model)
   except Exception as exc:self._log('rejected','materialize_error',job,error=str(exc)[:200]);continue
   self.active=Snapshot(job,payload);self._log('accepted','complete_snapshot',job,completed_ns=int(done_ns))
  if legal:
   need=(self.active is None or self.active.job.pair!=pair or frame-self.active.job.source_frame>=REFRESH_HOPS)
   inflight_same=self.inflight is not None and not self.inflight.revoked and self.inflight.pair==pair
   pending_same=self.pending is not None and self.pending.pair==pair
   if need and not inflight_same and not pending_same:self._new_pending(pair,int(prior_frame),int(prior_ns),int(evidence_frame),int(evidence_ns))
   if not need and self.pending is not None:self._log('superseded','active_sufficient',self.pending);self.pending=None
  return {'active':self.active,'pending':self.pending,'inflight':self.inflight,'status':'ready' if self.active is not None else ('invalid' if not legal else 'fallback_A'),'event_overflow':self.event_overflow}
