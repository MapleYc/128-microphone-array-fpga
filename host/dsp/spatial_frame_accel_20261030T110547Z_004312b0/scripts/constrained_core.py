"""Low-dimensional causal target-blocked GSC; no truth/scene inputs in DSP API."""
import math
import numpy as np
N=1024;HOP=512;FS=48000
PARAMETERS=dict(epsilon=.25,max_h_l1=2.,ema=.92,absolute_regularization=1e-6,
 relative_regularization=.01,min_target_power=.05,max_reference_ratio=2.,
 enabled_band_hz=[150,8000],sector_offsets_deg=[-5.,0.,5.],ranges_m=[1.2,3.,None])

def transfers(cfg,geom):
 p=geom.geometry();omega=2*np.pi*np.arange(513)/N;directions=cfg['directions']
 nominal_p=geom._propagation(p,directions['A'],FS)
 h=np.array([geom._transfer(p,nominal_p,directions[k],FS,omega) for k in ['A','B1','B2']])
 valid=np.abs(h[0])**2>=PARAMETERS['min_target_power']
 r=np.divide(h[1:],h[0],out=np.zeros((2,513),complex),where=valid[None,:])
 valid&=np.max(np.abs(r),axis=0)<=PARAMETERS['max_reference_ratio']
 f=np.arange(513)*FS/N;valid&=(f>=150)&(f<=8000)
 patch=[];labels=[]
 for da in PARAMETERS['sector_offsets_deg']:
  for de in PARAMETERS['sector_offsets_deg']:
   d={k:float(v) for k,v in directions['A'].items()};d['azimuth_deg']+=da;d['elevation_deg']+=de
   if any(abs(d[k])>90 for k in d):
    valid[:]=False;continue
   dot=geom._propagation(p,d,FS)*343/FS
   for distance in PARAMETERS['ranges_m']:
    if distance is None:incoming=dot*FS/343;amp=None
    else:
     length=np.sqrt(distance**2+2*distance*dot+np.sum(p*p,axis=1))
     incoming=(length-distance)*FS/343;amp=distance/length
    hv=np.array([geom._transfer(p,incoming,directions[k],FS,omega,incoming_amplitude=amp) for k in ['A','B1','B2']])
    ok=np.abs(hv[0])**2>=PARAMETERS['min_target_power'];valid&=ok
    z=hv[1:]-r*hv[0]
    patch.append(np.divide(z,hv[0],out=np.zeros_like(z),where=ok[None,:]));labels.append([da,de,distance])
 r[:,~valid]=0
 return dict(r=r,patch=np.array(patch),valid=valid,h_nominal=h,labels=labels)

class Spatial:
 def __init__(self,model,enabled=True):
  self.model=model;self.enabled=enabled;self.buffer=np.empty((3,0));self.tail=np.zeros(HOP)
  self.window=np.array([math.sqrt(max(0,.5-.5*math.cos(2*math.pi*k/N))) for k in range(N)])
  self.p1=np.zeros(513);self.p2=np.zeros(513);self.r12=np.zeros(513,complex)
  self.c1=np.zeros(513,complex);self.c2=np.zeros(513,complex);self.frame=0;self.rows=[]
 def push(self,three):
  three=np.asarray(three);assert three.shape[0]==3 and np.isfinite(three).all()
  self.buffer=np.concatenate([self.buffer,np.clip(three,-1,1)],axis=1);out=[]
  while self.buffer.shape[1]>=N:
   x=np.fft.rfft(self.buffer[:,:N]*self.window,axis=1);a=x[0]
   z=x[1:]-self.model['r']*a
   reg=PARAMETERS['absolute_regularization']+PARAMETERS['relative_regularization']*(self.p1+self.p2)
   r11=self.p1+reg;r22=self.p2+reg;det=r11*r22-abs(self.r12)**2
   good=self.model['valid']&np.isfinite(det)&(det>1e-12)
   h=np.zeros((2,513),complex)
   np.divide(self.c1*r22-self.c2*np.conj(self.r12),det,out=h[0],where=good)
   np.divide(self.c2*r11-self.c1*self.r12,det,out=h[1],where=good)
   h[:,~np.all(np.isfinite(h),axis=0)]=0
   l1=np.sum(abs(h),axis=0)
   change=np.max(abs(np.sum(self.model['patch']*h[None,:,:],axis=1)),axis=0)
   scale=np.minimum(1.,np.minimum(PARAMETERS['max_h_l1']/np.maximum(l1,1e-300),PARAMETERS['epsilon']/np.maximum(change,1e-300)))
   h*=scale
   if not self.enabled:h[:]=0
   bound=np.max(abs(np.sum(self.model['patch']*h[None,:,:],axis=1)),axis=0)
   output=a-np.sum(h*z,axis=0);output[0]=output[0].real;output[-1]=output[-1].real
   y=np.fft.irfft(output,n=N)*self.window;before=y[:HOP]+self.tail
   out.append(np.clip(before,-1,1).astype('<f4'));self.tail=y[HOP:]
   # Output uses strictly previous complete frames. Update only after emitting.
   prev=PARAMETERS['ema'] if self.frame else 0.;cur=1-prev
   self.p1=prev*self.p1+cur*abs(z[0])**2;self.p2=prev*self.p2+cur*abs(z[1])**2
   self.r12=prev*self.r12+cur*z[0]*np.conj(z[1])
   self.c1=prev*self.c1+cur*a*np.conj(z[0]);self.c2=prev*self.c2+cur*a*np.conj(z[1])
   self.rows.append(dict(frame=self.frame,input_start=self.frame*HOP,input_stop=self.frame*HOP+N,
    max_patch_response_change=float(bound.max()),max_h_l1=float(np.sum(abs(h),axis=0).max()),
    limited_bins=int(np.sum((scale<1)&good)),fallback_bins=int(np.sum(~good)),
    nominal_constraint_error=float(np.max(abs(np.sum(h*(self.model['h_nominal'][1:]-self.model['r']*self.model['h_nominal'][0]),axis=0)))),
    clamp_samples=int(np.sum(abs(before)>1)),output_peak=float(abs(before).max())))
   self.frame+=1;self.buffer=self.buffer[:,HOP:]
  return np.concatenate(out) if out else np.empty(0,dtype='<f4')

def run(three,model,packets=(200,),enabled=True):
 dsp=Spatial(model,enabled);outputs=[];i=0;j=0
 while i<three.shape[1]:
  n=packets[j%len(packets)];outputs.append(dsp.push(three[:,i:i+n]));i+=n;j+=1
 return np.concatenate(outputs),dsp.rows
