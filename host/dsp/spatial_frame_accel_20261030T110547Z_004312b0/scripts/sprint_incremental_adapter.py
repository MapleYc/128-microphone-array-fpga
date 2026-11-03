"""Independent incremental wrapper for the frozen offline-v2 signal path.

Decoded canonical MIC frames only; it neither consumes a DOA trace nor sees
future samples. It is a research adapter, not an RTL/firmware implementation.
"""
import math
from collections import OrderedDict
import numpy as np
import sprint_dynamic_pipeline as base
import sprint_blocked_power as blocked
from sprint_doa import InterferenceDOA

FS=48000; N=1024; HOP=512

class Incremental:
 def __init__(self,cfg,cache_capacity=128):
  self.cfg=cfg;self.pos=base.geom.geometry();self.beam=base.Beams(self.pos);self.taps_capacity=256;self.lock=-float(cfg['directions']['A']['azimuth_deg']);self.doa=InterferenceDOA(self.pos,lock_physical_az=self.lock)
  dref=base.geom._taps(self.pos,cfg['directions']['A'],FS)[2];self.max_lag=2*dref+2;self.capacity=N+self.max_lag;self.ring=np.zeros((128,self.capacity),dtype='<f4');self.received=0;self.next_start=0;self.tail=np.zeros(HOP);self.decision=None;self.cache=OrderedDict();self.cache_capacity=cache_capacity;self.window=np.sqrt(.5-.5*np.cos(2*np.pi*np.arange(N)/N));self.closed=False;self.atraces=[];self.trace=[];self.clip=0
 def _frame(self,start,physical):
  ii,cc=self.beam.taps_for(physical);use=self.beam.use;axis=start+np.arange(N);mic=np.zeros((len(use),N),dtype=float)
  for tap in range(4):
   ix=axis[None,:]-ii[use,None]-tap+1;valid=ix>=0
   if np.any(ix[valid]>=self.received):raise RuntimeError('future sample requested')
   if np.any(ix[valid] < self.received-self.capacity):raise RuntimeError('ring history overwritten')
   vals=self.ring[use[:,None],np.mod(ix,self.capacity)];mic+=cc[use,tap,None]*vals*valid
  out=np.mean(np.clip(mic,-1,1).astype(np.float32).astype(float),axis=0).astype('<f4');out[np.arange(start,start+N)<60]=0
  while len(self.beam.taps)>self.taps_capacity:self.beam.taps.pop(next(iter(self.beam.taps)))
  return out
 def _current(self):
  start=self.next_start;valid=bool(self.decision and self.decision['valid']);Aframe=self._frame(start,self.lock);self.atraces.append(Aframe.copy());A=np.fft.rfft(Aframe*self.window);gain=np.ones(513)
  if valid:
   physical=self.decision['azimuth_deg'];sign=1 if physical-self.lock>=0 else -1;other=physical+sign*20
   if abs(other)>85:other=physical-sign*20
   refs=[float(physical),float(other)];key=tuple(round(x,4) for x in refs)
   if key not in self.cache:
    self.cache[key]=blocked.calibrate(list(key),self.cfg,self.beam)
    if len(self.cache)>self.cache_capacity:self.cache.popitem(last=False)
   else:self.cache.move_to_end(key)
   model=self.cache[key];B=np.array([np.fft.rfft(self._frame(start,b)*self.window) for b in refs]);Z=B-model['rT']*A;NI=model['scale']*np.sum(abs(Z)**2,axis=0);PA=abs(A)**2;clean=np.maximum(PA-NI,0.);g=np.divide(clean,clean+NI,out=np.ones(513),where=(clean+NI)>np.finfo(float).eps);gain[model['valid']]=np.clip(g[model['valid']],.1,1.)
  y=np.fft.irfft(A*gain,n=N)*self.window;segment=y[:HOP]+self.tail;self.tail=y[HOP:];self.clip+=int(np.count_nonzero(abs(segment)>1));emitted=np.clip(segment,-1,1).astype('<f4')
  ix=np.mod(start+np.arange(N),self.capacity);observed=np.fft.rfft(self.ring[self.doa.channels[:,None],ix[None,:]].astype(float)*self.window,axis=1);self.decision=self.doa.process(observed);self.trace.append(self.decision.copy());self.next_start+=HOP
  return emitted
 def feed(self,decoded):
  if self.closed:raise RuntimeError('feed after finish')
  x=np.asarray(decoded);assert x.ndim==2 and x.shape[0]==128 and x.dtype==np.float32 and np.isfinite(x).all();at=0;out=[];self.atraces.clear();self.trace.clear()
  while at<x.shape[1]:
   need=self.next_start+N-self.received;take=min(need,x.shape[1]-at);assert take>0;offset=self.received%self.capacity;first=min(take,self.capacity-offset);self.ring[:,offset:offset+first]=x[:,at:at+first]
   if first<take:self.ring[:,:take-first]=x[:,at+first:at+take]
   at+=take;self.received+=take
   if self.received==self.next_start+N:out.append(self._current())
  return np.concatenate(out) if out else np.empty(0,dtype='<f4')
 def finish(self):
  self.closed=True;return np.empty(0,dtype='<f4')

def decode_words(words):
 """Input shape (sample frames, 128), signed24 sign-extended int32."""
 w=np.asarray(words);assert w.ndim==2 and w.shape[1]==128 and w.dtype==np.int32
 return ((w>>8).astype(np.float32)/np.float32(32768)).T
