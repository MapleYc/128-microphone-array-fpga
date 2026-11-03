"""Only replace constrained GSC's empirical h-L1 cap by microphone-noise gain."""
import math
import numpy as np
import constrained_core as prior
N,HOP,FS=prior.N,prior.HOP,prior.FS
PARAMETERS=dict(prior.PARAMETERS);PARAMETERS.pop('max_h_l1');PARAMETERS['maximum_output_white_noise_power_relative_A']=2.

def transfers(cfg,geom):
 model=prior.transfers(cfg,geom);p=geom.geometry();omega=2*np.pi*np.arange(513)/N
 weights=[];use=np.ones(128,bool);use[list(geom.MASK)]=False
 for key in ['A','B1','B2']:
  integer,c,_=geom._taps(p,cfg['directions'][key],FS)
  delays=integer[:,None]+np.arange(-1,3)[None,:]
  w=np.sum(c[None,:,:]*np.exp(-1j*omega[:,None,None]*delays[None,:,:]),axis=2)/int(use.sum());w[:,~use]=0;weights.append(w)
 weights=np.array(weights) # (beam,frequency,microphone)
 basis=np.concatenate([weights[:1],weights[1:]-model['r'][:,:,None]*weights[0][None,:,:]],axis=0)
 gram=np.einsum('ikm,jkm->kij',basis,np.conj(basis),optimize=True)
 assert np.isfinite(gram).all();assert np.max(abs(gram-np.conj(gram.swapaxes(1,2))))<1e-12
 model['noise_gram']=gram;model['beam_noise_gram']=np.einsum('ikm,jkm->kij',weights,np.conj(weights),optimize=True)
 return model

def project(h,model):
 gram=model['noise_gram'];p=gram[:,0,0].real
 q=np.einsum('ik,kij,jk->k',h,gram[:,1:,1:],np.conj(h),optimize=True).real
 b=np.real(np.sum(h*gram[:,1:,0].T,axis=0));q=np.maximum(q,0)
 allowance=(PARAMETERS['maximum_output_white_noise_power_relative_A']-1)*p
 disc=np.sqrt(np.maximum(0,b*b+q*allowance));snoise=np.ones(513)
 nz=q>1e-28
 positive=nz&(b>=0);negative=nz&(b<0)
 np.divide(b+disc,q,out=snoise,where=positive)
 np.divide(allowance,disc-b,out=snoise,where=negative)
 snoise=np.clip(snoise,0,1)
 change=np.max(abs(np.sum(model['patch']*h[None,:,:],axis=1)),axis=0)
 sdisk=np.minimum(1,PARAMETERS['epsilon']/np.maximum(change,1e-300))
 scale=np.minimum(snoise,sdisk)
 valid=model['valid']&np.isfinite(scale)&np.isfinite(p)&(p>1e-15)&np.isfinite(q)&np.isfinite(b)
 scale[~valid]=0;hh=h*scale
 q2=np.einsum('ik,kij,jk->k',hh,gram[:,1:,1:],np.conj(hh),optimize=True).real
 b2=np.real(np.sum(hh*gram[:,1:,0].T,axis=0))
 ratio=np.divide(p-2*b2+q2,p,out=np.ones(513),where=p>1e-15)
 assert np.isfinite(hh).all() and np.isfinite(ratio).all()
 return hh,dict(scale=scale,snoise=snoise,sdisk=sdisk,noise_power_ratio=ratio,raw_q=q,raw_b=b,p=p)

class Spatial(prior.Spatial):
 def push(self,three):
  three=np.asarray(three);assert three.shape[0]==3 and np.isfinite(three).all()
  self.buffer=np.concatenate([self.buffer,np.clip(three,-1,1)],axis=1);out=[]
  while self.buffer.shape[1]>=N:
   x=np.fft.rfft(self.buffer[:,:N]*self.window,axis=1);a=x[0];z=x[1:]-self.model['r']*a
   reg=PARAMETERS['absolute_regularization']+PARAMETERS['relative_regularization']*(self.p1+self.p2)
   r11=self.p1+reg;r22=self.p2+reg;det=r11*r22-abs(self.r12)**2
   good=self.model['valid']&np.isfinite(det)&(det>1e-12);h=np.zeros((2,513),complex)
   np.divide(self.c1*r22-self.c2*np.conj(self.r12),det,out=h[0],where=good)
   np.divide(self.c2*r11-self.c1*self.r12,det,out=h[1],where=good)
   h[:,~np.all(np.isfinite(h),axis=0)]=0
   h,diag=project(h,self.model)
   if not self.enabled:h[:]=0
   bound=np.max(abs(np.sum(self.model['patch']*h[None,:,:],axis=1)),axis=0)
   output=a-np.sum(h*z,axis=0);output[0]=output[0].real;output[-1]=output[-1].real
   y=np.fft.irfft(output,n=N)*self.window;before=y[:HOP]+self.tail
   out.append(np.clip(before,-1,1).astype('<f4'));self.tail=y[HOP:]
   prev=PARAMETERS['ema'] if self.frame else 0.;cur=1-prev
   self.p1=prev*self.p1+cur*abs(z[0])**2;self.p2=prev*self.p2+cur*abs(z[1])**2
   self.r12=prev*self.r12+cur*z[0]*np.conj(z[1]);self.c1=prev*self.c1+cur*a*np.conj(z[0]);self.c2=prev*self.c2+cur*a*np.conj(z[1])
   self.rows.append(dict(frame=self.frame,input_start=self.frame*HOP,input_stop=self.frame*HOP+N,
    max_patch_response_change=float(bound.max()),max_h_l1=float(np.sum(abs(h),axis=0).max()),max_noise_power_relative_A=float(diag['noise_power_ratio'].max()),
    noise_limited_bins=int(np.sum((diag['snoise']<diag['sdisk']-1e-12)&good)),disk_limited_bins=int(np.sum((diag['sdisk']<diag['snoise']-1e-12)&good)),
    limited_bins=int(np.sum((diag['scale']<1)&good)),fallback_bins=int(np.sum(~good)),
    nominal_constraint_error=float(np.max(abs(np.sum(h*(self.model['h_nominal'][1:]-self.model['r']*self.model['h_nominal'][0]),axis=0)))),
    clamp_samples=int(np.sum(abs(before)>1)),output_peak=float(abs(before).max())))
   self.frame+=1;self.buffer=self.buffer[:,HOP:]
  return np.concatenate(out) if out else np.empty(0,dtype='<f4')
def run(three,model,packets=(200,),enabled=True):
 dsp=Spatial(model,enabled);out=[];i=0;j=0
 while i<three.shape[1]:
  n=packets[j%len(packets)];out.append(dsp.push(three[:,i:i+n]));i+=n;j+=1
 return np.concatenate(out),dsp.rows
