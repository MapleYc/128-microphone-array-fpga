"""First end-to-end causal DOA/dynamic-reference/original-Wiener candidate."""
import os,sys
sys.dont_write_bytecode=True
for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[name]='1'
import argparse,datetime as dt,struct,hashlib,shutil,time,math
import user_layout_run as app
from user_layout_run import np,Path,load,dump,sha,geom,P
from sprint_doa import InterferenceDOA,PROFILE as DOA_PROFILE
ROOT=P/'dynamic_sprint_20261030T073500Z_918ca7bd';FS=48000;N=1024;HOP=512;CASES=['U01_solo','U02_pureI','U04_weak']
def init():
 ROOT.mkdir(exist_ok=False)
 for n in ['scripts','runs','tmp','integrity','audition']:(ROOT/n).mkdir()
 dump(ROOT/'PROTOCOL.yaml',dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(),sprint_start_utc='2026-10-30T07:25:00Z',sprint_summary_window_utc='2026-10-30T15:25:00Z',candidate='dynamic_reference_original_Wiener_v1',
  pipeline='Observed24MICFFT -> target-conditionedcausalSRP -> physicalIaz -> NEXTframe B1=I,B2=I+20degawayfromtarget(clip/switchifedge) -> raworiginalpowerWiener5+5 -> originalBP/LogMMSE/AGC.',
  unknown='Startunknown; no trainingquiet. WithoutvalidI useA unityWOLA,notassumeanotherpeakI. Keepconfiguredtargetlockfixed. Directionrequires3supportedpreviousframes,hold<=.5s.',
  beam='Actual4tapLagrange onall126healthymics,mask2/3,causaldelay30. ReferenceangleconstantperSTFTframe,chosenfrompreviousframeDOAdecision. OverlapWOLAcrossfadesactualfilteroutputs;alreadyemittedsamplesneverrewritten. Currentoutputhas1024windowavailabilitylatency,arrayspatialoffset30 preserved.',
  reference_independence='B1/B2differentanglesnotassumedindependent;directionupdatesandselectedfrequencyGramcorrelationreported. LowfreqTleakcanremain,raw5+5isonefixedcontrolnotsweep.',
  params=dict(wiener_b1=5,wiener_b2=5,doa=DOA_PROFILE),geometry='Actualfrozen128coords,physical-toHOSTangle signnegativeonplanararray. Sameheight/fronthemisphere initialscope. TrueIangle/range/activityonlygenerator/evaluation.',
  initial_gates=CASES,first_development_only=True,no_hardware_or_formal_changes=True,no_new_utterances_yet=True,fullpost='ExactoriginalBP/Log/AGC;oneU01Acalibrationperprofileallcases;nativelevelsretained. T/I/Qfromactualmixedgain/statefreezeonly,noindependentnonlinearpostsourceadditivity.',
  fallback_plan='IfraworiginalWienerfailsdueTleak,atmostonegeometrycalibratedblocked-referencepowerpathnext,innewprofile. No blinddouble5afterpowerremapping. Currentcodeisraworiginalpathonly.',
  data='128inputRAMonly,onecaseatatime;persistfewmonooutputs/components/DOAtrace/metadata;nofull128copiesorlargeaudiosets. NoJTAGFlashresetboard/permanentdelete.'))
 paths=[Path(__file__).absolute(),Path('sprint_doa.py').absolute(),Path('sprint_dynamic_eval.py').absolute(),Path('user_layout_run.py').absolute(),app.PHYS,app.B/'adapter/config/geometry_source.yaml',app.POST,app.ROOT/'DELIVERY_SHA256.yaml']
 dump(ROOT/'FREEZE.yaml',dict(protocol_sha=sha(ROOT/'PROTOCOL.yaml'),hashes={str(p):sha(p) for p in paths}))
 for n in ['sprint_dynamic_pipeline.py','sprint_doa.py','sprint_dynamic_eval.py','user_layout_run.py']:shutil.copy2(n,ROOT/'scripts'/n)
 print('DYNAMIC_PIPELINE_FROZEN',ROOT,flush=True)
def prepare(sid):
 old=app.ROOT/'runs'/sid;fi=load(old/'fixture.yaml');sc=fi['scene'];gen=load(old/'generator.yaml');cfg=load(old/'config.yaml');t,tm=app.source(sc,'target');i,im=app.source(sc,'interferer')
 if sc['role']=='target_only':i[:]=0
 if sc['role']=='interferer_only':t[:]=0
 i*=gen['interference_source_gain'];pos=geom.geometry();axis=np.arange(288000,dtype=float);bounds=np.array(gen['room']['bounds_m']);contexts=[app.physics._source_context(x,sc[who+'_az_deg'],sc[who+'_el_deg'],sc['distance_m' if j==0 else 'interferer_distance_m'],None,sc['rt60_s'],gen['room']['wall_amplitude_reflection'],bounds,sc['seed'],j) for j,(who,x) in enumerate([('target',t),('interferer',i)])]
 words=np.empty((288000,128),dtype='<i4');raw=np.empty((128,288000),dtype='<f4');ta=np.zeros(288000);ia=np.zeros(288000);ii,coeff,delay=geom._taps(pos,cfg['directions']['A'],FS);assert delay==30
 for m in range(128):
  if m in geom.MASK:words[:,m]=(1<<23)-1 if m==2 else -(1<<23);raw[m]=(words[:,m]>>8).astype(np.float32)/32768;continue
  a,_,_=app.physics._render_source(contexts[0],pos[m],axis,gen['mic_delay_errors_samples'][m],gen['room']['late_energy_ratio'],m);b,_,_=app.physics._render_source(contexts[1],pos[m],axis,gen['mic_delay_errors_samples'][m],gen['room']['late_energy_ratio'],m);gain=10**(gen['mic_gain_errors_db'][m]/20);a*=gain;b*=gain;mix=(a+b).astype(np.float32).astype(float)*gen['global_gain'];words[:,m]=np.clip(np.rint(mix*(1<<23)),-(1<<23),(1<<23)-1).astype(np.int32);raw[m]=(words[:,m]>>8).astype(np.float32)/32768;app.delay_add(ta,a*gen['global_gain'],int(ii[m]),coeff[m]);app.delay_add(ia,b*gen['global_gain'],int(ii[m]),coeff[m])
 header=struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',words.nbytes+36,b'WAVE',b'fmt ',16,1,128,FS,FS*128*4,128*4,32,b'data',words.nbytes);digest=hashlib.sha256(header);digest.update(memoryview(words).cast('B'));got=digest.hexdigest();assert got==fi['input_sha256'],(sid,'inputhash',got,fi['input_sha256']);del words
 ta/=126;ia/=126;ta[:60]=0;ia[:60]=0;actualA=np.fromfile(old/'fixed/beam_A.f32le',dtype='<f4');return raw,actualA,ta,ia,cfg,fi
class Beams:
 def __init__(self,positions):self.pos=positions;self.use=np.array([m for m in range(128) if m not in geom.MASK]);self.taps={}
 def taps_for(self,physical):
  key=round(float(physical),4)
  if key not in self.taps:self.taps[key]=geom._taps(self.pos,dict(azimuth_deg=-key,elevation_deg=0.),FS)[:2]
  return self.taps[key]
 def frame(self,raw,start,physical):
  ii,cc=self.taps_for(physical);axis=start+np.arange(N);mic=np.zeros((len(self.use),N))
  for tap in range(4):
   ix=axis[None,:]-ii[self.use,None]-tap+1;valid=(ix>=0)&(ix<raw.shape[1]);assert ix.max()<start+N;vals=raw[self.use[:,None],np.clip(ix,0,raw.shape[1]-1)];mic+=cc[self.use,tap,None]*vals*valid
  mic=np.clip(mic,-1,1).astype(np.float32);out=np.mean(mic.astype(float),axis=0).astype('<f4');out[np.arange(start,start+N)<60]=0;return out
 def correlation(self,angles):
  ks=np.array([4,16,32,64]);omega=2*np.pi*ks/N;ww=[]
  for a in angles:
   ii,cc=self.taps_for(a);dd=ii[self.use,None]+np.arange(-1,3)[None,:];ww.append(np.sum(cc[self.use][None,:,:]*np.exp(-1j*omega[:,None,None]*dd[None,:,:]),axis=2)/126)
  a,b=ww;rho=abs(np.sum(a*np.conj(b),axis=1))**2/(np.sum(abs(a)**2,axis=1)*np.sum(abs(b)**2,axis=1));return dict(hz=(ks*FS/N).tolist(),noise_squared_coherence=rho.tolist())
def run(sid):
 dest=ROOT/'runs'/sid
 if (dest/'COMPLETE.yaml').exists():print('DYNAMIC_ALREADY_COMPLETE',sid);return
 dest.mkdir(exist_ok=False);tic=time.perf_counter();raw,a,ta,ia,cfg,fi=prepare(sid);qa=a.astype(float)-ta-ia;win=np.sqrt(.5-.5*np.cos(2*np.pi*np.arange(N)/N));beam=Beams(geom.geometry());doa=InterferenceDOA(geom.geometry(),lock_physical_az=-cfg['directions']['A']['azimuth_deg']);decision=None;out=[];comps=[];ref=[];tails=np.zeros((5,HOP));trace=[];updates=[];last_pair=None;clamps=0;used=0
 for j,start in enumerate(range(0,raw.shape[1]-N+1,HOP)):
  valid=bool(decision and decision['valid']);physical=decision['azimuth_deg'] if valid else None;A=np.fft.rfft(a[start:start+N]*win);gain=np.ones(513);refs=None
  if valid:
   used+=1;sgn=1 if physical-doa.lock>=0 else -1;other=physical+sgn*20
   if abs(other)>85:other=physical-sgn*20
   refs=[float(physical),float(other)];B=np.array([np.fft.rfft(beam.frame(raw,start,b)*win) for b in refs]);den=abs(A)**2+5*np.sum(abs(B)**2,axis=0);gain=np.divide(abs(A)**2,den,out=np.zeros(513),where=np.isfinite(den)&(den>np.finfo(float).eps))
   key=tuple(round(x,1) for x in refs)
   if key!=last_pair:updates.append(dict(frame=j,refs_physical_deg=refs,geometry=beam.correlation(refs)));last_pair=key
  spectra=np.array([A,np.fft.rfft(ta[start:start+N]*win),np.fft.rfft(ia[start:start+N]*win),np.fft.rfft(qa[start:start+N]*win)]);z=np.fft.irfft(spectra*gain[None,:],n=N,axis=1)*win;reference=np.fft.irfft(spectra[1],n=N)*win;all5=np.concatenate([z,reference[None,:]],axis=0);segment=all5[:,:HOP]+tails;tails=all5[:,HOP:];clamps+=int((abs(segment[0])>1).sum());out.append(np.clip(segment[0],-1,1).astype('<f4'));comps.append(segment[1:4]);ref.append(segment[4]);observed=np.fft.rfft(raw[doa.channels,start:start+N].astype(float)*win,axis=1);newdecision=doa.process(observed);trace.append(dict(frame=j,input_window_end=start+N,applied_previous_doa_frame=None if decision is None else decision['frame'],spatial_active=valid,applied_physical_az=physical,refs_physical_deg=refs,mean_gain=float(gain.mean()),decision_next=newdecision));decision=newdecision
  if j and j%150==0:print('DYNAMIC_FRAMES',sid,j,'of561','active',used,flush=True)
 y=np.concatenate(out);c=np.concatenate(comps,axis=1);r=np.concatenate(ref);assert len(y)==287232 and np.isfinite(y).all();err=float(np.max(abs(y.astype(float)-c.sum(axis=0))));assert clamps==0 and err<=1e-7;assert all(t['applied_previous_doa_frame'] in [None,t['frame']-1] for t in trace)
 y.tofile(dest/'spatial.f32le')
 for k,v in zip(['T','I','Q'],c):v.astype('<f4').tofile(dest/('component_'+k+'.f32le'))
 r.astype('<f4').tofile(dest/'reference_T_A.f32le');dump(dest/'cfg.yaml',cfg);dump(dest/'fixture_pointer.yaml',dict(old_case=str(app.ROOT/'runs'/sid),input_sha256=fi['input_sha256'],alignment=dict(spatial=30,post=2078),causal_input='rawdecoded128only,truthcomponentsONLYfrozenmixedgain evaluation',candidate='dynamic_reference_original_Wiener_v1'));dump(dest/'doa_trace.yaml',trace);dump(dest/'reference_updates.yaml',updates);dump(dest/'metadata.yaml',dict(doa=doa.metadata(),active_frames=used,total_frames=len(trace),active_fraction=used/len(trace),component_additivity_max_error=err,spatial_clamps=clamps,reference_strategy='I and20degaway,twobutnotassumedindependent',first_active_frame=next((t['frame'] for t in trace if t['spatial_active']),None),last_decision=decision,wall_seconds=time.perf_counter()-tic,source_truth_not_in_DSP=True));dump(dest/'COMPLETE.yaml',dict(spatial_sha=sha(dest/'spatial.f32le'),seconds=time.perf_counter()-tic));print('DYNAMIC_SPATIAL_COMPLETE',sid,'active',used,'first',next((t['frame'] for t in trace if t['spatial_active']),None),'seconds',round(time.perf_counter()-tic,2),flush=True)
if __name__=='__main__':
 if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
 p=argparse.ArgumentParser();p.add_argument('phase',choices=['init','run']);p.add_argument('--id',choices=CASES);a=p.parse_args();init() if a.phase=='init' else run(a.id)
