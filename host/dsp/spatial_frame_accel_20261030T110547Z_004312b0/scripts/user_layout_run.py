"""Frozen profiles at user-provided 2--3 m radii, 2 m separation. No DSP edits."""
import os,sys
sys.dont_write_bytecode=True
for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[key]='1'
import io,copy,math,time,subprocess,shutil,datetime as dt
from scipy.io import wavfile
from noisegain_run import P,B,G,L,OLD,geom,core,load,dump,sha,module,aux,quality,np,signal,Path
ROOT=P/'user_layout_20261030T055600Z_a3484c9d';FS=48000
PHYS=G/'scripts/tuning_physics.py';POST=OLD/'post_adapter/build/post_probe.exe';PROBE=G/'build128/golden_probe.exe'
physics=module('layout_physics',PHYS)
TEXTS={
 'dev_target': 'A botanical garden is preparing a small exhibition about plants that grow beside streams. The gardener sorts several seed packets and describes where each plant was found. A visitor asks how the roots survive when the ground becomes flooded. They compare a drawing with a living specimen and notice that the leaves have different shapes near the base. Before the demonstration ends, the gardener places the pots beside a window and writes the watering schedule on a card. The exhibition will open next week, after the paths have been checked and the labels attached. Several volunteers will guide visitors through the shaded section of the garden.',
 'dev_interferer': 'A carpenter is repairing an old wooden chair in a quiet workshop. The legs are sound, but one joint has loosened after many years of use. She removes the damaged glue and tests the pieces without forcing them together. Her apprentice measures a thin strip of wood that will fill the gap. While the repair dries, they discuss the marks left by earlier tools under the seat. Those marks help explain how the original maker assembled the frame. Later they will smooth the repaired surface and compare its color with the surrounding wood. The owner hopes to use the chair at a family gathering.',
 'val_target': 'At a small railway station, an engineer explains how rainwater is guided away from the platform. The channels must remain clear so that a sudden storm does not flood the entrance. A maintenance worker lifts a cover and checks for leaves caught beneath it. They record the depth of the channel and inspect a cracked edge near the steps. A replacement piece will be fitted before the busy season begins. On the other side of the station, passengers are reading a notice about an upcoming timetable change. The engineer finishes the inspection and returns the tools to a numbered compartment in the service room.',
 'val_interferer': 'A ceramic artist is unpacking a group of bowls after their final firing. Each piece has cooled overnight, and the surface colors are now easy to compare. One bowl has a narrow blue line that follows the curve of its rim. Another has a pale glaze with several tiny spots near the base. The artist checks for cracks by turning each piece slowly beneath a bright lamp. Her assistant photographs the bowls beside a scale and writes down the order in which they left the kiln. A few samples will remain in the studio as references for the next batch of work.'}
def angle(rt,ri):return math.degrees(math.acos((rt*rt+ri*ri-4)/(2*rt*ri)))
def scene(sid,rt=2.5,ri=2.5,sir=0,role='mixture',group='development',**kw):
 d=dict(id=sid,distance_m=rt,interferer_distance_m=ri,separation_m=2.,target_az_deg=0.,target_el_deg=0.,interferer_az_deg=angle(rt,ri),interferer_el_deg=0.,sir_db=sir,role=role,group=group,rt60_s=0.,seed=20261030100+int(sid[1:3]),preroll_s=.6,source_gain=.5,evaluation_window_s=[.8,3.8],sir_window_seconds=[.8,3.8],target_start_s=1.,interferer_start_s=5.,source_family='new_text_local_Zira_same_synthetic_voice')
 d.update(kw);lock=float(d.pop('lock_host',0.));d['runtime_control']={k:dict(azimuth_deg=lock+off,elevation_deg=0.) for k,off in [('A',0),('B1',-30),('B2',30)]};return d
def plan():
 out=[scene('U01_solo',role='target_only'),scene('U02_pureI',role='interferer_only'),scene('U03_mix0'),scene('U04_weak',sir=-8)]
 for n,(rt,ri) in enumerate([(2.,2.),(3.,3.),(2.,3.)]):
  for j,sir in enumerate([0,-8]):out.append(scene('U%02d_mix_%s_%s_%s'%(5+2*n+j,rt,ri,sir),rt,ri,sir,group='validation',target_start_s=9.,interferer_start_s=13.))
 out.extend([scene('U11_steer4',sir=-8,group='validation',lock_host=-4.,target_start_s=9.,interferer_start_s=13.),scene('U12_room',sir=-8,group='validation',rt60_s=.3,mic_gain_std_db=.75,mic_delay_std_samples=.1,target_start_s=9.,interferer_start_s=13.)]);return out
def init():
 ROOT.mkdir(exist_ok=False)
 for n in ['scripts','tmp','speech','runs','integrity','audition']:(ROOT/n).mkdir()
 names=['user_layout_run.py','noisegain_core.py','constrained_core.py','adaptive_solo_aux.py','tuning_quality_audition.py','constrained_run.py','constrained_post_eval.py','noisegain_run.py']
 for name in names:shutil.copy2(name,ROOT/'scripts'/name)
 shutil.copy2(PHYS,ROOT/'scripts/tuning_physics.py');shutil.copy2(L/'scripts/leakage_coefficients.py',ROOT/'scripts/leakage_coefficients.py')
 dump(ROOT/'PROTOCOL.yaml',dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(),user='Each radius approximately2--3m, separation2m. Heights/orientation/roomunknown.',assumptions='Sameheight/el0;arrayfrontnormal+z, targetonaxis, Ipositivexzangle; spherical exactR/r delays; existinghybridroomonlysensitivity notmeasuredroom.',profiles=['original_fixed_5_5','noisegain_gamma2_disk025','A_same_post'],no_tuning=True,scenes=plan(),parameters=core.PARAMETERS,causal='OnlyconfiguredHOSTlockAandfixedflanks+-30, knownactualgeometry/mask/taps, causal3beams. Sourceangles/ranges/cleanlabels ONLYgenerator/evaluation; nooracleIsteering.',metrics='Spatialmixedgain/h frozenonto linearT/I/Q fromactualrender;no nonlinearpostsourceadditivity. FullpostSI/corr/gates are proxies, pureI/solo directobservations. One U01calibrationgain perprofile appliedallcases, no percaseRMSnormalization.',decision='No new acoustic pass threshold. Joint targetfidelity and audibleI reduction evidence, previously6/3/3 onlyexploratory. Previousnarrowanglefailures retainedinpriorreports.',source_texts=TEXTS,budget='12six-secondcases, no raw128persisted; reusebuilds,4briefnewtextSAPI; only3representativeWAVsets. Originalrootsimmutable.'))
 paths=[PROBE,POST,PHYS,B/'adapter/config/geometry_source.yaml',OLD/'DELIVERY_SHA256.yaml',P/'noisegain_20261030T043400Z_c37124e9/DELIVERY_SHA256.yaml']+[Path(n).absolute() for n in names]
 dump(ROOT/'FREEZE.yaml',dict(time=dt.datetime.now(dt.timezone.utc).isoformat(),hashes={str(p):sha(p) for p in paths},protocol_sha256=sha(ROOT/'PROTOCOL.yaml'),profiles_frozen_before_new_speech=True))
 print('LAYOUT_FROZEN',ROOT,'cases',len(plan()),flush=True)
def source(sc,who):
 key=('dev_' if sc['group']=='development' else 'val_')+who;p=ROOT/'speech'/(key+'.wav');fs,w=wavfile.read(p);assert fs==FS and w.dtype==np.int16 and w.ndim==1
 a=w[round(sc[who+'_start_s']*FS):round((sc[who+'_start_s']+3.4)*FS)].astype(float)/32768;assert len(a)==163200
 mean=float(a.mean());a-=mean;scale=.025*sc['source_gain']/max(float(np.std(a[9600:153600])),1e-30);a*=scale;y=np.zeros(288000);y[28800:28800+len(a)]=a
 return y,dict(path=str(p),sha256=sha(p),slice_start_s=sc[who+'_start_s'],duration_s=3.4,pre_roll_s=.6,removed_clip_mean=mean,normalization_scale=scale,voice='MicrosoftZira localSAPI,notindependenthumans')
def env():
 e=os.environ.copy();e['PATH']=str(__import__('pathlib').Path(__file__).resolve().parent / '../../../runtime') + __import__('os').pathsep+e.get('PATH','');e['TEMP']=e['TMP']=str(ROOT/'tmp');return e
def post(src,cfg,dest):
 cmd=[str(POST),'--input',str(src),'--config',str(cfg),'--output',str(dest),'--gain-summary-only']
 with dest.with_suffix('.log').open('x',encoding='utf-8') as f:p=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,env=env(),timeout=90)
 assert p.returncode==0;md=load(dest/'metadata.yaml')
 for v in md['stages'].values():assert sha(dest/v['file'])==v['sha256']
def delay_add(acc,x,integer,coeff,cast=False):
 n=len(x);out=np.zeros(n)
 for k in range(4):
  lag=integer+k-1
  if lag>=0:out[lag:]+=coeff[k]*x[:n-lag] if lag else coeff[k]*x
  else:out[:lag]+=coeff[k]*x[-lag:]
 acc+=out.astype(np.float32).astype(float) if cast else out
def components(sc,t,i,gen,cfg,actual):
 pos=geom.geometry();axis=np.arange(len(t),dtype=float);room=gen['room'];bounds=np.array(room['bounds_m']);rt=sc['rt60_s'];rho=room['wall_amplitude_reflection']
 ctx=[physics._source_context(s,sc[who+'_az_deg'],0.,sc['distance_m' if j==0 else 'interferer_distance_m'],None,rt,rho,bounds,sc['seed'],j) for j,(who,s) in enumerate([('target',t),('interferer',i*gen['interference_source_gain'])])]
 taps=[geom._taps(pos,cfg['directions'][key],FS)[:2] for key in ['A','B1','B2']];bt=np.zeros_like(actual,dtype=float);bi=np.zeros_like(bt);predict=np.zeros_like(bt)
 for ch in range(128):
  if ch in [2,3]:continue
  a,_,_=physics._render_source(ctx[0],pos[ch],axis,gen['mic_delay_errors_samples'][ch],room['late_energy_ratio'],ch);b,_,_=physics._render_source(ctx[1],pos[ch],axis,gen['mic_delay_errors_samples'][ch],room['late_energy_ratio'],ch);gain=10**(gen['mic_gain_errors_db'][ch]/20)
  mixed=((a+b)*gain).astype(np.float32).astype(float)*gen['global_gain'];word=np.clip(np.rint(mixed*(1<<23)),-(1<<23),(1<<23)-1).astype(np.int32);decoded=(word>>8).astype(np.float32)/np.float32(32768)
  for j,(integers,c) in enumerate(taps):
   delay_add(bt[j],a*gain*gen['global_gain'],int(integers[ch]),c[ch]);delay_add(bi[j],b*gain*gen['global_gain'],int(integers[ch]),c[ch]);delay_add(predict[j],decoded.astype(float),int(integers[ch]),c[ch],True)
 predict=(predict/126).astype('<f4');bt/=126;bi/=126;predict[:,:60]=0;bt[:,:60]=0;bi[:,:60]=0
 err=float(np.max(abs(predict-actual)));assert np.array_equal(predict,actual),(sc['id'],err)
 return bt,bi,actual.astype(float)-bt-bi,dict(three_beams_byteexact=True,max_error=err,linear_components='T/I unquantized physicalbeam plusQ=actual-T-I. Freezeactualmixedfilter only; no independentnonlinearpostadditivity.')
def spatial_components(three,bt,bi,q,model,expected):
 win=np.sqrt(.5-.5*np.cos(2*np.pi*np.arange(1024)/1024));dsp=core.Spatial(model);tails={k:np.zeros(512) for k in ['fixed','gsc','beam']};outs={k:[] for k in tails}
 # matrix channel axis contains mixed,T,I,Q. Exactsameh/gain fromactualmixedhistory.
 for start in range(0,three.shape[1]-1023,512):
  xs=np.array([np.fft.rfft(x[:,start:start+1024]*win,axis=1) for x in [three,bt,bi,q]]);x=xs[0];a=x[0];z=x[1:]-model['r']*a
  reg=1e-6+.01*(dsp.p1+dsp.p2);r11=dsp.p1+reg;r22=dsp.p2+reg;det=r11*r22-abs(dsp.r12)**2;good=model['valid']&np.isfinite(det)&(det>1e-12);h=np.zeros((2,513),complex)
  np.divide(dsp.c1*r22-dsp.c2*np.conj(dsp.r12),det,out=h[0],where=good);np.divide(dsp.c2*r11-dsp.c1*dsp.r12,det,out=h[1],where=good);h[:,~np.all(np.isfinite(h),axis=0)]=0;h,_=core.project(h,model)
  power=abs(a)**2;gain=power/(power+5*abs(x[1])**2+5*abs(x[2])**2+1e-30)
  for kind in outs:
   if kind=='gsc':v=xs[:,0]-np.sum(h[None,:,:]*(xs[:,1:]-model['r'][None,:,:]*xs[:,0,None,:]),axis=1)
   elif kind=='fixed':v=xs[:,0]*gain
   else:v=xs[:,0]
   v[:,0]=v[:,0].real;v[:,-1]=v[:,-1].real;y=np.fft.irfft(v,n=1024,axis=1)*win;before=y[:,:512]+tails[kind];outs[kind].append(before);tails[kind]=y[:,512:]
  prev=.92 if dsp.frame else 0.;cur=1-prev;dsp.p1=prev*dsp.p1+cur*abs(z[0])**2;dsp.p2=prev*dsp.p2+cur*abs(z[1])**2;dsp.r12=prev*dsp.r12+cur*z[0]*np.conj(z[1]);dsp.c1=prev*dsp.c1+cur*a*np.conj(z[0]);dsp.c2=prev*dsp.c2+cur*a*np.conj(z[1]);dsp.frame+=1
 out={k:np.concatenate(v,axis=1) for k,v in outs.items()};checks={}
 for k,v in out.items():
  checks[k]=dict(mixed_replay_max_abs_error=float(np.max(abs(v[0]-expected[k]))),additivity_max_abs_error=float(np.max(abs(v[0]-v[1:].sum(axis=0)))))
  assert checks[k]['mixed_replay_max_abs_error']<2e-7 and checks[k]['additivity_max_abs_error']<1e-11,(k,checks[k])
 return out,checks
def run(group):
 frozen=load(ROOT/'WRAPPER_FIX_FREEZE.yaml')
 for p,h in frozen['hashes'].items():assert sha(p)==h,p
 for sc in [s for s in load(ROOT/'PROTOCOL.yaml')['scenes'] if s['group']==group]:
  dest=ROOT/'runs'/sc['id']
  if (dest/'COMPLETE_v2.yaml').exists():continue
  dest.mkdir(exist_ok=True);tic=time.perf_counter();t,tm=source(sc,'target');i,im=source(sc,'interferer')
  if sc['role']=='target_only':i[:]=0
  if sc['role']=='interferer_only':t[:]=0
  cfg=load(P/'noisegain_20261030T043400Z_c37124e9/runs/T1_solo_quiet/config.yaml');cfg['directions']=sc['runtime_control']
  if not (dest/'config.yaml').exists():dump(dest/'config.yaml',cfg)
  else:assert load(dest/'config.yaml')==cfg
  if (dest/'fixed/metadata.yaml').exists():
   md=load(dest/'fixed/metadata.yaml');inputsha=md['input_sha256'];gen=load(dest/'generator.yaml');axis=np.arange(len(t));truth=dict(target_direct_center=np.interp(axis-240,axis,t,left=0.,right=0.)*gen['global_gain'],interferer_direct_center=np.interp(axis-240,axis,i,left=0.,right=0.)*gen['interference_source_gain']*gen['global_gain'])
  else:
   pcm,truth,gen,rirs=physics.render(t,i,geom.geometry(),sc);buf=io.BytesIO();wavfile.write(buf,FS,pcm);blob=buf.getvalue();del pcm,buf;dump(dest/'generator.yaml',gen)
   cmd=[str(PROBE),'--input','-','--config',str(dest/'config.yaml'),'--output',str(dest/'fixed'),'--seconds','6']
   with (dest/'fixed.log').open('x',encoding='utf-8') as f:f.write(str(cmd)+'\n');f.flush();p=subprocess.run(cmd,input=blob,stdout=f,stderr=subprocess.STDOUT,env=env(),timeout=120)
   inputsha=__import__('hashlib').sha256(blob).hexdigest();del blob;assert p.returncode==0;md=load(dest/'fixed/metadata.yaml');assert md['input_sha256']==inputsha
  three=np.array([np.fromfile(dest/'fixed'/(k+'.f32le'),dtype='<f4') for k in ['beam_A','beam_B1','beam_B2']]);model=core.transfers(cfg,geom);gsc,rows=core.run(three,model);beam=aux.unity(three[0]).astype('<f4')
  if not (dest/'gsc.f32le').exists():gsc.tofile(dest/'gsc.f32le')
  else:assert np.array_equal(np.fromfile(dest/'gsc.f32le',dtype='<f4'),gsc)
  if not (dest/'spatial_trace_summary.yaml').exists():dump(dest/'spatial_trace_summary.yaml',dict(max_noise=max(r['max_noise_power_relative_A'] for r in rows),max_disk=max(r['max_patch_response_change'] for r in rows),max_h_l1=max(r['max_h_l1'] for r in rows),clamp_samples=sum(r['clamp_samples'] for r in rows)))
  if (dest/'beam').exists():
   bm=load(dest/'beam/metadata.yaml')
   if bm['stages']['final']['sample_count']!=len(beam):(dest/'beam').rename(dest/'beam_INVALID_FLOAT64');(dest/'beam.log').rename(dest/'beam_INVALID_FLOAT64.log')
  if not (dest/'beam_VALID_FLOAT32.f32le').exists():beam.tofile(dest/'beam_VALID_FLOAT32.f32le')
  else:assert np.array_equal(np.fromfile(dest/'beam_VALID_FLOAT32.f32le',dtype='<f4'),beam)
  assert (dest/'beam_VALID_FLOAT32.f32le').stat().st_size==len(beam)*4
  for kind in ['gsc','beam']:
   if not (dest/kind/'metadata.yaml').exists():post(dest/('beam_VALID_FLOAT32.f32le' if kind=='beam' else 'gsc.f32le'),dest/'config.yaml',dest/kind)
   assert load(dest/kind/'metadata.yaml')['stages']['final']['sample_count']==len(beam)
  bt,bi,q,proof=components(sc,t,i,gen,cfg,three);out,checks=spatial_components(three,bt,bi,q,model,dict(fixed=np.fromfile(dest/'fixed/wiener.f32le',dtype='<f4'),gsc=gsc,beam=beam))
  fi=dict(scene=sc,source=dict(target=tm,interferer=im),input_sha256=inputsha,input_transport='RAM_RIFF_NOT_PERSISTED',beam_component_proof=proof,component_filter_replay=checks)
  if not (dest/'fixture.yaml').exists():dump(dest/'fixture.yaml',fi)
  else:assert load(dest/'fixture.yaml')==fi
  evaluate(sc,dest,truth,out)
  dump(dest/'COMPLETE_v2.yaml',dict(seconds=time.perf_counter()-tic,completed=dt.datetime.now(dt.timezone.utc).isoformat(),samples=288000))
  print('LAYOUT_CASE_COMPLETE',sc['id'],'seconds',round(time.perf_counter()-tic,2),flush=True)
def rms(x):return float(np.sqrt(np.mean(x*x))) if len(x) else None
def db(a,b):return float(20*np.log10(max(a,1e-30)/max(b,1e-30)))
def evaluate(sc,dest,truth,out):
 a,b=38400,182400;sos=np.vstack([signal.butter(2,100,'highpass',fs=FS,output='sos'),signal.butter(2,8000,'lowpass',fs=FS,output='sos')]);reference=signal.sosfilt(sos,truth['target_direct_center'])[a:b];it=signal.sosfilt(sos,truth['interferer_direct_center'])[a:b]
 et=np.mean(reference.reshape(-1,960)**2,axis=1);ei=np.mean(it.reshape(-1,960)**2,axis=1);masks={'all':np.ones(b-a,bool),'I_dominant':np.repeat((ei>=10*et)&(ei>=max(float(ei.max())*.001,1e-30)),960),'T_dominant':np.repeat((et>=10*ei)&(et>=max(float(et.max())*.001,1e-30)),960)};result=[]
 for kind in ['fixed','gsc','beam']:
  cp=out[kind][:,a+30:b+30];ref=out['beam'][1,a+30:b+30];cpbp=np.array([signal.sosfilt(sos,v)[a+30:b+30] for v in out[kind]])
  spatial=dict(native_T_rms=rms(cp[1]),native_I_rms=rms(cp[2]),native_Q_rms=rms(cp[3]),T_gain_relative_A_db=db(rms(cp[1]),rms(ref)),target_fidelity=quality.fidelity(cp[1],ref),target_shape=quality.solo_shape(cp[1],ref) if sc['role']!='interferer_only' else None,
   component_SIR_db=db(rms(cp[1]),rms(cp[2])) if sc['role']=='mixture' else None,bandpassed_component_SIR_db=db(rms(cpbp[1]),rms(cpbp[2])) if sc['role']=='mixture' else None,component_I_at_equal_T_rms=rms(cp[2])*rms(ref)/max(rms(cp[1]),1e-30) if sc['role']=='mixture' else None)
  stages={}
  for st in ['post_bandpass','post_logmmse','post_agc','final']:
   y=np.fromfile(dest/kind/(st+'.f32le'),dtype='<f4').astype(float);offset=30 if st=='post_bandpass' else 2078;seg=y[a+offset:b+offset];assert len(seg)==len(reference)
   stages[st]=dict(target=quality.fidelity(seg,reference),rms=rms(seg),peak=float(abs(seg).max()),nonfinite=int(np.sum(~np.isfinite(y))),rail_samples=int(np.sum(abs(y)==np.float32(10**(-1/20)))),samples=len(y),gates={k:dict(seconds=float(mask.sum()/FS),total_output_rms=rms(seg[mask])) for k,mask in masks.items()})
  result.append(dict(profile=kind,scene=sc['id'],role=sc['role'],spatial=spatial,post=stages))
 dump(dest/'metrics_v2.yaml',result)
 print('LAYOUT_METRICS',sc['id'],[(r['profile'],r['spatial']['bandpassed_component_SIR_db'],r['post']['final']['target']['si_sdr_db']) for r in result],flush=True)
if __name__=='__main__':
 if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
 if sys.argv[1]=='init':init()
 else:run(sys.argv[1])
