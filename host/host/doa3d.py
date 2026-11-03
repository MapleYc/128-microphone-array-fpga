"""Causal 3D target-conditioned SRP, fixed 4deg grid; no source truth input.

Extends the frozen horizontal tracker to az/el and both target tangent modes.
Only a complete current observed window is read; decisions apply next hop.
"""
import math,time
import numpy as np
from sprint_doa import select_channels,PROFILE
from direction3d import direction,unit,from_unit,angle,arc_clear

class InterferenceDOA3D:
    def __init__(self,positions,lock,fs=48000,n=1024,hop=512):
        self.lock=direction(lock);self.fs=fs;self.n=n;self.hop=hop
        self.channels=select_channels(positions,count=24);self.positions=np.asarray(positions)[self.channels].copy()
        hz=np.fft.rfftfreq(n,1/fs);self.bins=np.flatnonzero((hz>=750)&(hz<=4000));self.omega=2*np.pi*hz[self.bins]
        self.grid=np.array([(a,e) for a in np.arange(-80.,80.1,4.) for e in np.arange(-40.,40.1,4.)])
        self.units=np.array([unit(d) for d in self.grid]);self.allowed=np.array([angle(d,self.lock)>10. for d in self.grid])
        a,e=self.lock
        # Fixed engineering uncertainty span, not a user acoustic acceptance:
        # protect the 3x3 +/-4deg target steering neighbourhood plus 3m target.
        # This is explicit 2D angular modelling, not an elevation projection.
        self.target_offsets=[(da,de) for da in (-4.,0.,4.) for de in (-4.,0.,4.)]
        modes=np.stack([self._steering((a+da,e+de)) for da,de in self.target_offsets]+[self._steering(self.lock,3.)],axis=-1)
        self.target_u=np.zeros_like(modes);self.ranks=[]
        for k,m in enumerate(modes):
            u,s,_=np.linalg.svd(m,full_matrices=False);r=int((s>max(1e-12,s[0]*1e-9)).sum());self.target_u[k,:,:r]=u[:,:r];self.ranks.append(r)
        d=np.array([self._steering(d) for d in self.grid]);c=np.einsum('fmr,dfm->dfr',self.target_u.conj(),d);d-=np.einsum('fmr,dfr->dfm',self.target_u,c)
        p=np.sum(abs(d)**2,axis=-1);self.dictionary=np.divide(d,np.sqrt(p)[:,:,None],out=np.zeros_like(d),where=p[:,:,None]>1e-12);self.dictionary_valid=p>1e-12
        # Immutable conjugate views avoid allocating/copying the 23MB dictionary
        # on every audio hop. Same complex128 reductions and grid as v1.
        self.target_conjugate=np.conj(self.target_u)
        self.dictionary_conjugate=np.conj(self.dictionary)
        self.target_conjugate.setflags(write=False);self.dictionary_conjugate.setflags(write=False)
        # The 48k audio/frame path remains continuous. 3D search itself has a
        # fixed 4-hop (23.4375Hz) causal cadence; held directions do not create
        # fresh evidence or reset the snapshot evidence age.
        self.search_interval_hops=4
        self.reset()
    def _steering(self,d,distance=None):
        u=unit(d)
        if distance is None:delay=-(self.positions@u)/343.;amp=np.ones(len(self.channels))
        else:r=np.linalg.norm(distance*u-self.positions,axis=1);delay=(r-distance)/343.;amp=distance/r
        return amp[None,:]*np.exp(-1j*self.omega[:,None]*delay)
    def reset(self):
        self.frame=0;self.score=np.zeros(len(self.grid));self.total=0.;self.residual=0.;self.pending=None;self.confirm=0;self.held=None;self.misses=0;self.last_result=None;self.last_search_frame=None
    def process(self,mic_fft):
        begin=time.perf_counter_ns()
        if self.frame%self.search_interval_hops:
            self.misses+=1
            valid=self.held is not None and self.misses<=math.ceil(.5*self.fs/self.hop)
            result=dict(self.last_result,frame=self.frame,available_after_input_samples=self.frame*self.hop+self.n,applies_from_output_frame=self.frame+1,valid=valid,evidence_valid=False,search_performed=False,last_search_frame=self.last_search_frame,held_without_fresh_evidence=valid,compute_ms=(time.perf_counter_ns()-begin)/1e6)
            if not valid:result.update(azimuth_deg=None,elevation_deg=None)
            self.frame+=1;return result
        x=np.asarray(mic_fft)
        if x.shape!=(len(self.channels),self.n//2+1) or not np.iscomplexobj(x) or not np.isfinite(x).all():raise ValueError('3D tracker spectrum shape/nonfinite')
        x=x[:,self.bins].T.astype(np.complex128);coeff=np.einsum('fmr,fm->fr',self.target_conjugate,x);z=x-np.einsum('fmr,fr->fm',self.target_u,coeff)
        total=np.sum(abs(x)**2,axis=1);res=np.sum(abs(z)**2,axis=1);matched=np.einsum('dfm,fm->df',self.dictionary_conjugate,z)
        coherence=np.divide(abs(matched)**2,res[None,:],out=np.zeros_like(matched.real),where=res[None,:]>1e-24);coherence=np.clip(coherence,0,1);coherence[~self.dictionary_valid]=0
        active=(total>=max(float(total.max())*1e-4,1e-24))&(res>=max(float(res.max())*1e-4,1e-24));weights=np.maximum(coherence[self.allowed].max(axis=0)-.12,0)*active
        instant=coherence@weights/max(float(weights.sum()),1e-24);alpha=.8 if self.frame else 0.;self.score=alpha*self.score+(1-alpha)*instant;self.total=alpha*self.total+(1-alpha)*float(total.sum());self.residual=alpha*self.residual+(1-alpha)*float(res.sum())
        index=int(np.argmax(np.where(self.allowed,self.score,-np.inf)));candidate=tuple(self.grid[index]);dist=np.rad2deg(np.arccos(np.clip(self.units@self.units[index],-1,1)));competitor=self.allowed&(dist>10.);peak=float(self.score[index]);other=float(self.score[competitor].max()) if competitor.any() else 0.
        bins=np.argmax(np.where(self.allowed[:,None],coherence,-np.inf),axis=0);support=float(weights[dist[bins]<=10.].sum()/max(float(weights.sum()),1e-24));fraction=self.residual/max(self.total,1e-24)
        checks=dict(active_bins=int(active.sum())>=6,residual_power=fraction>=.01,peak_score=peak>=.30,peak_margin=peak-other>=.008,peak_ratio=peak/max(other,1e-12)>=1.02,frequency_support=support>=.35);evidence=all(checks.values())
        if evidence:
            self.confirm=self.confirm+1 if self.pending is not None and angle(candidate,self.pending)<=4. else 1;self.pending=candidate
            if self.confirm>=3:
                if self.held is None:self.held=candidate
                elif arc_clear(self.held,candidate,self.lock):
                    delta=angle(candidate,self.held);fraction_step=min(.25,4./max(delta,1e-12));u=(1-fraction_step)*unit(self.held)+fraction_step*unit(candidate);next_d=from_unit(u)
                    if angle(next_d,self.lock)>10.:self.held=next_d
                else:self.held=candidate
                self.misses=0
            else:self.misses+=1
        else:self.pending=None;self.confirm=0;self.misses+=1
        valid=self.held is not None and self.misses<=math.ceil(.5*self.fs/self.hop);held=self.held if valid else None
        self.last_search_frame=self.frame
        result=dict(frame=self.frame,available_after_input_samples=self.frame*self.hop+self.n,applies_from_output_frame=self.frame+1,apply_only_next_hop=True,valid=valid,azimuth_deg=None if held is None else held[0],elevation_deg=None if held is None else held[1],raw_candidate_azimuth_deg=candidate[0],raw_candidate_elevation_deg=candidate[1],evidence_valid=evidence,pending_confirmations=self.confirm,held_without_fresh_evidence=valid and not evidence,checks=checks,peak_score=peak,peak_margin=peak-other,frequency_support_fraction=support,residual_power_fraction=fraction,compute_ms=(time.perf_counter_ns()-begin)/1e6,grid_directions=len(self.grid),search_performed=True,last_search_frame=self.frame);self.last_result=result.copy();self.frame+=1;return result
    def metadata(self):return dict(scope='3D software candidate, not board acoustic acceptance',grid_step_deg=4,grid_directions=len(self.grid),dictionary_bytes=self.dictionary.nbytes,lock=list(self.lock),target_modes='3x3 +/-4deg far target neighbourhood plus 3m centre',target_offsets=self.target_offsets,ranks=self.ranks,search_interval_hops=self.search_interval_hops,search_rate_hz=self.fs/self.hop/self.search_interval_hops)
