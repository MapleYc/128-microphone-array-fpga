"""Experimental original NumPy calibration with intra-request reuse.

Only the *current* two references are used. Preserve original finite-window
FFT, sensor selection, normalization, thresholds and mathematical order. No
audio integration or future-direction precomputation occurs here.
"""
import numpy as np
import sprint_blocked_power as old

def calibrate(refs,cfg,beam=None,*,debug=False):
 p=old.geom.geometry();omega=2*np.pi*np.arange(513)/old.N
 directions=[cfg['directions']['A']]+[dict(azimuth_deg=-float(a),elevation_deg=0.) for a in refs]
 use_mask=np.ones(len(p),dtype=bool);use_mask[list(old.geom.MASK)]=False
 beams=[]
 for d in directions:
  integer,c,_=old.geom._taps(p,d,old.FS);delays=integer[:,None]+np.arange(-1,3)[None,:]
  beams.append(np.sum(c[None,:,:]*np.exp(-1j*omega[:,None,None]*delays[None,:,:]),axis=2))
 incidents=[]
 for d in (cfg['directions']['A'],dict(azimuth_deg=-float(refs[0]),elevation_deg=0.)):
  radius=3.;dot=old.geom._propagation(p,d,old.FS)*343/old.FS;length=np.sqrt(radius**2+2*radius*dot+np.sum(p*p,axis=1));incoming=(length-radius)*old.FS/343;amplitude=radius/length
  incident=np.exp(-1j*omega[:,None]*incoming[None,:]);incident=incident*np.asarray(amplitude)[None,:];incidents.append(incident)
 fields=[np.array([np.sum((incident*spatial)[:,use_mask],axis=1)/int(use_mask.sum()) for spatial in beams]) for incident in incidents]
 win=np.sqrt(.5-.5*np.cos(2*np.pi*np.arange(old.N)/old.N));power=abs(np.fft.fft(win))**2;power/=power.sum();kernel=np.fft.fft(power)
 def covariance(H):
  h=old.fullreal(H);return np.array([[old.convfreq(h[q]*np.conj(h[r]),kernel)[:513] for r in range(3)] for q in range(3)])
 CT,CI=[covariance(h) for h in fields];pT=CT[0,0].real;rT=np.divide(CT[1:,0],pT[None,:],out=np.zeros((2,513),complex),where=pT[None,:]>.05)
 def blocked_trace(C):return np.maximum(np.sum(C[[1,2],[1,2]].real-2*np.real(np.conj(rT)*C[1:,0])+abs(rT)**2*C[0,0].real[None,:],axis=0),0.)
 zi=blocked_trace(CI);pai=np.maximum(CI[0,0].real,0.);scale=np.divide(pai,zi,out=np.zeros(513),where=zi>1e-30)
 use=np.array([m for m in range(128) if m not in old.geom.MASK]);W=np.array([q[:,use]/126 for q in beams]);gram=np.einsum('qfm,rfm->qrf',W,np.conj(W),optimize=True);gn=np.zeros_like(gram)
 for q in range(3):
  for r in range(3):gn[q,r]=old.convfreq(old.fullreal(gram[q,r]),kernel)[:513]
 noise=np.divide(scale*blocked_trace(gn),gn[0,0].real,out=np.full(513,np.inf),where=gn[0,0].real>1e-15);f=np.arange(513)*old.FS/old.N;valid=(f>=100)&(f<=8000)&(pT>.05)&(zi>1e-4*np.maximum(pai,1e-20))&np.isfinite(noise)&(noise<=16)&np.isfinite(scale)
 result=dict(rT=rT,scale=scale,valid=valid,noise_gain=noise,pI_model=pai,zI_model=zi,diagnostics=dict(refs_physical_deg=refs,valid_bins=int(valid.sum()),valid_fraction=float(valid.mean()),maximum_valid_reference_noise_gain=float(noise[valid].max()) if valid.any() else None,maximum_target_regression_ratio=float(abs(rT).max()),ranges_are_fixed_assumptions_not_truth=True,model='Windowconvolutionofactual4tapsteeringpowersunderlocallyflatsourcespectrum;notexactcoloredspeechtransfer.'))
 if debug:return result,dict(H_target=fields[0],H_interferer=fields[1],CT=CT,CI=CI,W=W,gram=gram,GN=gn,pT=pT,beams=np.array(beams),incidents=np.array(incidents),window=win,power=power,kernel=kernel)
 return result
