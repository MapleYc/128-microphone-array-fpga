"""Second allowed path: window-calibrated blocked-reference power Wiener."""
import sprint_dynamic_pipeline as base
from sprint_dynamic_pipeline import *
ROOT=P/'dynamic_blocked_power_20261030T080000Z_9abe7c03'
PARAMETERS=dict(assumed_target_distance_m=3.,assumed_interferer_distance_m=3.,gain_floor=.1,maximum_reference_estimate_noise_gain=16.,minimum_relative_blocked_I_power=1e-4,enabled_band_hz=[100.,8000.],coefficient_after_mapping=1.,power_ema=None)
def convfreq(x,kernel_fft):return np.fft.ifft(np.fft.fft(x,axis=-1)*kernel_fft,axis=-1)
def fullreal(h):return np.concatenate([h,np.conj(h[..., -2:0:-1])],axis=-1)
def calibrate(refs,cfg,beam):
 p=geom.geometry();omega=2*np.pi*np.arange(513)/N;directions=[cfg['directions']['A']]+[dict(azimuth_deg=-float(a),elevation_deg=0.) for a in refs];fields=[]
 for direction in [cfg['directions']['A'],dict(azimuth_deg=-float(refs[0]),elevation_deg=0.)]:
  radius=3.;dot=geom._propagation(p,direction,FS)*343/FS;length=np.sqrt(radius**2+2*radius*dot+np.sum(p*p,axis=1));incoming=(length-radius)*FS/343;amplitude=radius/length;H=np.array([geom._transfer(p,incoming,d,FS,omega,incoming_amplitude=amplitude) for d in directions]);fields.append(H)
 win=np.sqrt(.5-.5*np.cos(2*np.pi*np.arange(N)/N));power=abs(np.fft.fft(win))**2;power/=power.sum();kernel=np.fft.fft(power)
 def covariance(H):
  h=fullreal(H);return np.array([[convfreq(h[q]*np.conj(h[r]),kernel)[:513] for r in range(3)] for q in range(3)])
 CT,CI=[covariance(h) for h in fields];pT=CT[0,0].real;rT=np.divide(CT[1:,0],pT[None,:],out=np.zeros((2,513),complex),where=pT[None,:]>.05)
 def blocked_trace(C):return np.maximum(np.sum(C[[1,2],[1,2]].real-2*np.real(np.conj(rT)*C[1:,0])+abs(rT)**2*C[0,0].real[None,:],axis=0),0.)
 zi=blocked_trace(CI);pai=np.maximum(CI[0,0].real,0.);scale=np.divide(pai,zi,out=np.zeros(513),where=zi>1e-30)
 use=np.array([m for m in range(128) if m not in geom.MASK]);W=[]
 for d in directions:
  ii,cc,_=geom._taps(p,d,FS);delay=ii[use,None]+np.arange(-1,3)[None,:];W.append(np.sum(cc[use][None,:,:]*np.exp(-1j*omega[:,None,None]*delay[None,:,:]),axis=2)/126)
 W=np.array(W);gram=np.einsum('qfm,rfm->qrf',W,np.conj(W),optimize=True);gn=np.zeros_like(gram)
 for q in range(3):
  for r in range(3):gn[q,r]=convfreq(fullreal(gram[q,r]),kernel)[:513]
 noise=np.divide(scale*blocked_trace(gn),gn[0,0].real,out=np.full(513,np.inf),where=gn[0,0].real>1e-15);f=np.arange(513)*FS/N;valid=(f>=100)&(f<=8000)&(pT>.05)&(zi>1e-4*np.maximum(pai,1e-20))&np.isfinite(noise)&(noise<=16)&np.isfinite(scale)
 return dict(rT=rT,scale=scale,valid=valid,noise_gain=noise,pI_model=pai,zI_model=zi,diagnostics=dict(refs_physical_deg=refs,valid_bins=int(valid.sum()),valid_fraction=float(valid.mean()),maximum_valid_reference_noise_gain=float(noise[valid].max()) if valid.any() else None,maximum_target_regression_ratio=float(abs(rT).max()),ranges_are_fixed_assumptions_not_truth=True,model='Windowconvolutionofactual4tapsteeringpowersunderlocallyflatsourcespectrum;notexactcoloredspeechtransfer.'))
def init():
 ROOT.mkdir(exist_ok=False)
 for n in ['scripts','runs','tmp','integrity','audition']:(ROOT/n).mkdir()
 dump(ROOT/'PROTOCOL.yaml',dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(),candidate='dynamic_calibrated_blocked_power_Wiener_v2',parameters=PARAMETERS,
  direction='Reuseexactfrozenprevious-framecausaldecisionsfromrawv1,sameverifiedrawinputSHA. DOAindependentofoutputfilter,notGT/noentireclipDOAbackfill.',
  target_block='rTj=conv(H_TBj*conj(H_TA),windowPower)/conv(abs(H_TA)^2,windowPower);Zj=Bj-rTj*A. Knownactual4tap/geometry andconfiguredlock,constant3m assumption,localwhite/stationarywindowmodel. NotfitcurrentB/A.',
  I_mapping='P_AI_model=CI_AA, P_ZI_model=sumdiag(blockCI). NIhat=P_AI_model/P_ZI_model*(abs(Z1)^2+abs(Z2)^2). CI fromestimatedI direction+fixed3m,nottrueangle/distance. Sharedsensornoisecovexplicitincalibration;referenceNI-estimatenoisegain<=16,nearzeroinvalid->unity.',
  filter='ConventionalWienercleanpower=max(PA-NIhat,0),G=clip(clean/(clean+NIhat),.1,1). Noold5coefficient,nooverallmute. UnknownDOA->A,allout-of-modelbins->A. Gainusescurrentavailablecompletedframe;noEMA/newquiettraining.',
  limits='Spectralmaskcanstilldamagetargetinoverlap;smallDOA/range/window/colorerrorsaffectmapping. Noisegain16isinternalNIestimatorcapnotclaimoutputMICnoiseamplification:outputG*A<=Aperframe. DoesnotresolveLFseparabilitybudget. Originalpostunchanged.',
  gates=CASES,original_v1_root=str(base.ROOT),post='SameoriginalBPLogAGC+sameU01oneconstantanchor/actualT-I-Qfrozenmixedgain.',data='No128inputpersisted,newmonooutputs+fewWAVonly;noformalRTLCPU/board/permanentdelete.'))
 paths=[Path(__file__).absolute(),Path('sprint_dynamic_pipeline.py').absolute(),Path('sprint_dynamic_eval.py').absolute(),Path('sprint_doa.py').absolute(),base.ROOT/'PROTOCOL.yaml',app.ROOT/'DELIVERY_SHA256.yaml']
 for sid in CASES:paths += [base.ROOT/'runs'/sid/'doa_trace.yaml',base.ROOT/'runs'/sid/'fixture_pointer.yaml']
 dump(ROOT/'FREEZE.yaml',dict(hashes={str(p):sha(p) for p in paths},protocol_sha=sha(ROOT/'PROTOCOL.yaml')))
 for n in ['sprint_blocked_power.py','sprint_dynamic_pipeline.py','sprint_dynamic_eval.py','sprint_doa.py']:shutil.copy2(n,ROOT/'scripts'/n)
 print('BLOCKED_POWER_FROZEN',ROOT,flush=True)
def run(sid):
 dest=ROOT/'runs'/sid
 if (dest/'COMPLETE.yaml').exists():print('BLOCKED_ALREADY_COMPLETE',sid);return
 dest.mkdir(exist_ok=False);tic=time.perf_counter();prior=base.ROOT/'runs'/sid;trace=load(prior/'doa_trace.yaml');ptr=load(prior/'fixture_pointer.yaml');assert ptr['input_sha256']==load(app.ROOT/'runs'/sid/'fixture.yaml')['input_sha256'];assert all(x['applied_previous_doa_frame'] in [None,x['frame']-1] for x in trace)
 if not any(x['spatial_active'] for x in trace):
  for name in ['spatial.f32le','component_T.f32le','component_I.f32le','component_Q.f32le','reference_T_A.f32le','cfg.yaml']:shutil.copy2(prior/name,dest/name)
  dump(dest/'fixture_pointer.yaml',dict(ptr,candidate='dynamic_calibrated_blocked_power_Wiener_v2',reused_exact_unity_fallback=str(prior)));dump(dest/'metadata.yaml',dict(all_doa_invalid=True,unity_fallback_reused_byteexact=True,doa_trace=str(prior/'doa_trace.yaml'),source_truth_not_in_DSP=True));dump(dest/'COMPLETE.yaml',dict(spatial_sha=sha(dest/'spatial.f32le'),seconds=time.perf_counter()-tic));print('BLOCKED_UNITY_BYTEEXACT',sid,flush=True);return
 raw,a,ta,ia,cfg,fi=base.prepare(sid);qa=a.astype(float)-ta-ia;win=np.sqrt(.5-.5*np.cos(2*np.pi*np.arange(N)/N));beam=base.Beams(geom.geometry());cache={};out=[];comps=[];ref=[];tails=np.zeros((5,HOP));details=[];clamps=0;updates=[]
 for j,start in enumerate(range(0,raw.shape[1]-N+1,HOP)):
  tr=trace[j];gain=np.ones(513);A=np.fft.rfft(a[start:start+N]*win);diag=None
  if tr['spatial_active']:
   refs=tr['refs_physical_deg'];key=tuple(round(x,4) for x in refs)
   if key not in cache:cache[key]=calibrate(list(key),cfg,beam);updates.append(dict(first_frame=j,**cache[key]['diagnostics']))
   model=cache[key];B=np.array([np.fft.rfft(beam.frame(raw,start,b)*win) for b in refs]);Z=B-model['rT']*A;NI=model['scale']*np.sum(abs(Z)**2,axis=0);PA=abs(A)**2;clean=np.maximum(PA-NI,0.);g=np.divide(clean,clean+NI,out=np.ones(513),where=(clean+NI)>np.finfo(float).eps);gain[model['valid']]=np.clip(g[model['valid']],.1,1.);diag=dict(NI_over_PA_band_median=float(np.median(NI[model['valid']]/np.maximum(PA[model['valid']],1e-30))),valid_bins=int(model['valid'].sum()))
  spectra=np.array([A,np.fft.rfft(ta[start:start+N]*win),np.fft.rfft(ia[start:start+N]*win),np.fft.rfft(qa[start:start+N]*win)]);z=np.fft.irfft(spectra*gain[None,:],n=N,axis=1)*win;reference=np.fft.irfft(spectra[1],n=N)*win;all5=np.concatenate([z,reference[None,:]]);segment=all5[:,:HOP]+tails;tails=all5[:,HOP:];clamps+=int((abs(segment[0])>1).sum());out.append(np.clip(segment[0],-1,1).astype('<f4'));comps.append(segment[1:4]);ref.append(segment[4]);details.append(dict(frame=j,causal_doa_frame=tr['applied_previous_doa_frame'],active=tr['spatial_active'],mean_gain=float(gain.mean()),floor_bins=int((gain==.1).sum()),calibration=diag))
 y=np.concatenate(out);c=np.concatenate(comps,axis=1);r=np.concatenate(ref);err=float(np.max(abs(y.astype(float)-c.sum(axis=0))));assert clamps==0 and err<=1e-7 and np.isfinite(y).all() and len(y)==287232;y.tofile(dest/'spatial.f32le')
 for k,v in zip(['T','I','Q'],c):v.astype('<f4').tofile(dest/('component_'+k+'.f32le'))
 r.astype('<f4').tofile(dest/'reference_T_A.f32le');dump(dest/'cfg.yaml',cfg);dump(dest/'fixture_pointer.yaml',dict(ptr,candidate='dynamic_calibrated_blocked_power_Wiener_v2',reused_causal_doa_trace=str(prior/'doa_trace.yaml')));dump(dest/'gain_trace.yaml',details);dump(dest/'calibration_updates.yaml',updates);dump(dest/'metadata.yaml',dict(components_additive_max_error=err,spatial_clamps=clamps,calibration_count=len(cache),active_frames=sum(t['spatial_active'] for t in trace),doa_trace=str(prior/'doa_trace.yaml'),parameters=PARAMETERS,source_truth_not_in_DSP=True,wall_seconds=time.perf_counter()-tic));dump(dest/'COMPLETE.yaml',dict(spatial_sha=sha(dest/'spatial.f32le'),seconds=time.perf_counter()-tic));print('BLOCKED_SPATIAL_COMPLETE',sid,'models',len(cache),'seconds',round(time.perf_counter()-tic,2),flush=True)
if __name__=='__main__':
 if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['init','run']);p.add_argument('--id',choices=CASES);a=p.parse_args();init() if a.phase=='init' else run(a.id)
